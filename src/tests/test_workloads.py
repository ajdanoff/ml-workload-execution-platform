import asyncio
import pytest
from src.app.services import queue, broken_s3_inference_worker, suicidal_inference_worker
from src.app.migrations import async_session, Workload, OutboxEvent
from src.app.main import request_counts

# Helper header block dictionary matching standard security middleware requirements
AUTH_HEADERS = {"Authorization": "Bearer valid-token"}
FOX_IMAGE = "https://wikimedia.org"


# =====================================================================
# Cases 1-3: Positive Scenarios
# =====================================================================

@pytest.mark.positive
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-1111",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {"confidence": 0.35, "max_frames": 1000}
    }
])
async def test_case_1_successful_workload_creation(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 202
    body = response.json()
    assert "task_id" in body
    assert body["status"] == "queued"
    assert isinstance(body["task_id"], str)

@pytest.mark.positive
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-2222",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE
    }
])
async def test_case_2_omission_of_optional_params(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 202
    body = response.json()
    assert "task_id" in body
    assert body["status"] == "queued"

@pytest.mark.positive
@pytest.mark.asyncio
@pytest.mark.parametrize("mock_task_id", ["test-task-uuid-abcde"])
async def test_case_3_query_existing_workload_status(client, mock_task_id):
    # Pre-seed active testing baseline record directly into database sandbox via global async session
    async with async_session() as session:
        async with session.begin():
            session.add(Workload(
                task_id=mock_task_id,
                idempotency_key="seed-key-3333",
                model="yolov8s",
                input_uri=FOX_IMAGE,
                status="running"
            ))
            
    response = await client.get(f"/workloads/{mock_task_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == mock_task_id
    assert body["status"] == "running"
    assert body["result"] is None

# =====================================================================
# Cases 4-9: Negative & Boundary Conditions
# =====================================================================

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-4040",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "unknown_malicious_field": "data-leak" 
    }
])
async def test_case_4_extra_fields_forbidden(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert any(err["type"] == "extra_forbidden" for err in body["detail"])

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "unique-key-5555", "input_uri": FOX_IMAGE}
])
async def test_case_5_missing_required_model_field(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "required" in body["message"]

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "unique-key-6666", "model": "unknown", "input_uri": FOX_IMAGE}
])
async def test_case_6_unsupported_model_architecture_type(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "MODEL_NOT_SUPPORTED"

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "unique-key-7777", "model": "yolov8s", "input_uri": FOX_IMAGE, "params": {"confidence": 1.01, "max_frames": 100}}
])
async def test_case_7_confidence_out_of_bounds(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "unique-key-8888", "model": "yolov8s", "input_uri": FOX_IMAGE, "params": {"confidence": 0.35, "max_frames": 0}}
])
async def test_case_8_max_frames_out_of_bounds(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "unique-key-9999", "model": "yolov8s", "input_uri": "invalid-http-uri-path/video.mp4"}
])
async def test_case_9_invalid_input_uri_format(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

# =====================================================================
# Cases 10-12: Idempotency Contracts
# =====================================================================

@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "unique-key-1010", "model": "yolov8s", "input_uri": FOX_IMAGE}
])
async def test_case_10_repeated_request_same_key(client, payload):
    res1 = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert res1.status_code == 202
    task_id_1 = res1.json()["task_id"]
    
    res2 = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert res2.status_code == 202
    assert res2.json()["task_id"] == task_id_1

@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload1, payload2", [
    ({"idempotency_key": "shared-key-1111", "model": "yolov8s", "input_uri": FOX_IMAGE},
     {"idempotency_key": "shared-key-1111", "model": "yolov8m", "input_uri": FOX_IMAGE})
])
async def test_case_11_same_key_different_payload_conflict(client, payload1, payload2):
    await client.post("/workloads", json=payload1, headers=AUTH_HEADERS)
    res2 = await client.post("/workloads", json=payload2, headers=AUTH_HEADERS)
    assert res2.status_code == 409
    assert res2.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"

@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "race-key-1212", "model": "yolov8s", "input_uri": FOX_IMAGE}
])
async def test_case_12_concurrent_identical_requests(client, payload):
    t1 = client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    t2 = client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["task_id"] == r2.json()["task_id"]

# =====================================================================
# Cases 13-15: Authorization & Rate Limits
# =====================================================================

@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "auth-1313", "model": "yolov8s", "input_uri": FOX_IMAGE}
])
async def test_case_13_missing_authorization_token(client, payload):
    response = await client.post("/workloads", json=payload)
    assert response.status_code == 401

@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "auth-1414", "model": "yolov8s", "input_uri": FOX_IMAGE}
])
async def test_case_14_token_insufficient_permissions(client, payload):
    response = await client.post("/workloads", json=payload, headers={"Authorization": "Bearer token-no-scope"})
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_PERMISSIONS"

@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "rate-1515", "model": "yolov8s", "input_uri": FOX_IMAGE}
])
async def test_case_15_rate_limit_exceeded(client, payload):
    request_counts["127.0.0.1"] = 101  # Trip cluster request boundary limits
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMIT_EXCEEDED"

# =====================================================================
# Cases 16-18: Real End-to-End Infrastructure Testing
# =====================================================================

@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "infra-key-1616", "model": "yolov8s", "input_uri": FOX_IMAGE}
])
async def test_case_16_queue_infrastructure_unreachable(client, payload):
    # Pass an explicit test control header to trigger the 503 fallback cleanly
    response = await client.post(
        "/workloads",
        json=payload,
        headers={**AUTH_HEADERS, "X-Simulate-Broker-Outage": "true"}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "BROKER_UNAVAILABLE"

# In src/tests/test_workloads.py (Case 17)
@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1717",
        "model": "yolov8s",
        "input_uri": "https://amazonaws.com/missing-file" # Aligned with missing-file text
    }
])
async def test_case_17_s3_object_missing_async_failure(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    
    status_body = {}
    for _ in range(30):
        await asyncio.sleep(0.1)
        res = await client.get(f"/workloads/{task_id}", headers=AUTH_HEADERS)
        status_body = res.json()
        if status_body.get("status") == "failed":
            break
            
    assert status_body["status"] == "failed"
    assert "INPUT_NOT_FOUND" in status_body["result"]["error"]

@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1818",
        "model": "yolov8s",
        "input_uri": "https://amazonaws.com/suicidal-crash" # Chaos trigger text
    }
])
async def test_case_18_worker_process_node_crashes_mid_inference(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    
    status_body = {}
    for _ in range(30):
        await asyncio.sleep(0.1)
        res = await client.get(f"/workloads/{task_id}", headers=AUTH_HEADERS)
        status_body = res.json()
        # RabbitMQ's manual acknowledgment loop will re-queue the task when the worker dies.
        # Because our fake-model is stateless, a fresh worker will pick it up and process it.
        # Thus, we verify the task safely recovers and finishes as 'completed'.
        if status_body.get("status") == "completed":
            break
            
    assert status_body["status"] == "completed"
    assert "detected_objects_count" in status_body["result"]
