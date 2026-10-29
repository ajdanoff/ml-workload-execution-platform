import pytest
import asyncio
from src.app.services import queue, broken_s3_inference_worker, suicidal_inference_worker, db_store
from src.app.main import request_counts

# Helper header block dictionary
AUTH_HEADERS = {"Authorization": "Bearer valid-token"}

import logging

logger = logging.getLogger()

FOX_IMAGE = "https://thumb.wikimedia.org/wikipedia/commons/thumb/7/78/Red_Fox_Portrait.jpg/960px-Red_Fox_Portrait.jpg"


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
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_1_successful_workload_creation(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    # Assertions
    assert response.status_code == 202
    
    body = response.json()
    assert "task_id" in body
    assert body["status"] == "accepted"
    
    # Structural type validation (Check if it's returning a clean string)
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
    # Payload omitting the "params" key completely
    
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    # Assertions
    assert response.status_code == 202
    body = response.json()
    assert "task_id" in body
    assert body["status"] == "accepted"

from src.app.services import db_store

# =====================================================================
# Case 3: Fixed Null Assertion
# =====================================================================
@pytest.mark.positive
@pytest.mark.asyncio
@pytest.mark.parametrize("mock_task_id", [
    "test-task-uuid-abcde",
    "test-task-uuid-fghij"
])
async def test_case_3_query_existing_workload_status(client, mock_task_id):
    db_store[mock_task_id] = {
        "status": "running",
        "payload": {
            "model": "yolov8s",
            "input_uri": FOX_IMAGE
        },
        "result": None
    }
    
    response = await client.get(
        f"/workloads/{mock_task_id}",
        headers=AUTH_HEADERS
    )
    
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == mock_task_id
    assert body["status"] == "running"
    assert body["result"] is None  # Fixed from == None for robust dictionary parsing

# =====================================================================
# Cases 4-9: Negative & Boundary Conditions
# =====================================================================

# =====================================================================
# Case 4: Extra Fields Verification
# =====================================================================
@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-4040",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "unknown_malicious_field": "anomaly-data-leak"  # Extra field target
    }
])
async def test_case_4_extra_fields_forbidden(client, payload):
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    # Verify that the Pydantic error details capture the "extra_forbidden" type
    assert any(err["type"] == "extra_forbidden" for err in body["detail"])

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-4444",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_5_missing_required_model_field(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    # Assertions
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "required" in body["message"]

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-6666",
        "model": "unknown",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_6_unsupported_model_architecture_type(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    # Assertions
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "MODEL_NOT_SUPPORTED"
    assert "unsupported" in body["message"]
    

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-7777",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 1.01,
            "max_frames": 100
        }
    }
])
async def test_case_7_confidence_out_of_bounds(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    # Assertions
    assert response.status_code == 422
    
    # Verify that response body explicitly highlights the validation error path
    body = response.json()
    assert "detail" in body
    assert "confidence" in body["detail"][0]["loc"]
    assert "less than or equal to 1" in body["detail"][0]["msg"]

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-8888",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 0
        }
    }
])
async def test_case_8_max_frames_out_of_bounds(client, payload):
    
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    # Assertions
    assert response.status_code == 422
    
    # Verify that response body explicitly highlights the validation error path
    body = response.json()
    assert "detail" in body
    assert "max_frames" in body["detail"][0]["loc"]
    assert "greater than or equal to 1" in body["detail"][0]["msg"]

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-9999",
        "model": "yolov8s",
        "input_uri": "invalid-http-uri-path/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_9_invalid_input_uri_format(client, payload):
    
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
    
    # Assertions
    assert response.status_code == 422
    
    # Verify that response body explicitly highlights the validation error path
    body = response.json()
    assert "detail" in body
    assert "input_uri" in body["detail"][0]["loc"]
    assert "Input should be a valid URL" in body["detail"][0]["msg"]

# =====================================================================
# Cases 10-12: Idempotency
# =====================================================================

@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-1010",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_10_repeated_request_same_key(client, payload):
    """Scenario: Repeated request with identical idempotency key
       Input: Identical payload and same "idempotency_key"
       Expectation: 202 Accepted
       Verification: Returns the exact original task_id and does not spawn duplicate worker processing.
       CI: Yes
    """
    
    # 1. Execute the initial API transaction call
    first_response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token-admin"}
    )
    
    assert first_response.status_code == 202
    first_body = first_response.json()
    first_task_id = first_body["task_id"]
    
    # 2. Re-send the exact same payload layout with identical idempotency
    second_response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token-admin"}
    )
        
    assert second_response.status_code == 202
    second_body = second_response.json()
    assert second_body["task_id"] == first_task_id

import asyncio

# =====================================================================
# Case 11: Idempotency Key Conflict Payload Code Check
# =====================================================================
@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload1, payload2", [
    ({"idempotency_key": "shared-key-1111", "model": "yolov8s", "input_uri": FOX_IMAGE},
     {"idempotency_key": "shared-key-1111", "model": "yolov8m", "input_uri": FOX_IMAGE}
    )
])
async def test_case_11_same_key_different_payload_conflict(client, payload1, payload2):
    await client.post("/workloads", json=payload1, headers={"Authorization": "Bearer valid-token-admin"})
    res2 = await client.post("/workloads", json=payload2, headers={"Authorization": "Bearer valid-token-admin"})
    
    assert res2.status_code == 409
    body = res2.json()
    # Updated to verify code response correctly
    assert body["code"] == "IDEMPOTENCY_KEY_CONFLICT"

@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"idempotency_key": "race-key-1212", "model": "yolov8s", "input_uri": FOX_IMAGE}
])
@pytest.mark.asyncio
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
    {
        "idempotency_key": "auth-1313",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_13_missing_authorization_token(client, payload):
    response = await client.post(
        "/workloads",
        json=payload
    )
    
    assert response.status_code == 401
    body = response.json()
    assert "detail" in body
    assert body["detail"] == "Missing Authorization Header"

# =====================================================================
# Case 14: Fixed Permissive Scope Exception Structure Parsing
# =====================================================================
# =====================================================================
# Case 14: Permissions Error Checking
# =====================================================================
@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "auth-1414",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE
    }
])
async def test_case_14_token_insufficient_permissions(client, payload):
    response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer token-no-scope"}
    )
    
    assert response.status_code == 403
    body = response.json()
    # No more complex string deserialization needed; it's a clean, direct dictionary
    assert body["code"] == "INSUFFICIENT_PERMISSIONS"
    assert "scopes" in body["message"]
    
@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "rate-1515",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_15_rate_limit_exceeded(client, payload):
    # The HTTPX client connects locally as 127.0.0.1
    request_counts["127.0.0.1"] = 101  
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 429

# =====================================================================
# Cases 16-20: Infrastructure, Downstream Workers & Lifecycle
# =====================================================================

# =====================================================================
# Case 16: Fixed Mismatched Broker Unavailable Payload Code Checks
# =====================================================================
@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1616",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE
    }
])
async def test_case_16_queue_infrastructure_unreachable(client, payload):
    queue.is_connected = False
    
    response = await client.post(
        "/workloads",
        json=payload,
        headers=AUTH_HEADERS
    )
        
    assert response.status_code == 503
    body = response.json()
    # Updated to align with the new machine-readable error contract matrix
    assert body["code"] == "BROKER_UNAVAILABLE"
    
@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1717",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_17_s3_object_missing_async_failure(client, payload):
    queue.worker_function = broken_s3_inference_worker
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 202
    task_id = response.json()["task_id"]
        
    # Poll until the asynchronous worker updates the task state
    status_body = {}
    for _ in range(10):
        await asyncio.sleep(0.1)
        status_res = await client.get(f"/workloads/{task_id}", headers=AUTH_HEADERS)
        status_body = status_res.json()
        if status_body["status"] in ["completed", "failed"]:
            break
            
    assert status_body["status"] == "failed"
    assert "INPUT_NOT_FOUND" in status_body["result"]["error"]
    
# =====================================================================
# Case 18: Fixed System Process Crash Exception Naming Verification
# =====================================================================
@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1818",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE
    }
])
async def test_case_18_worker_process_node_crashes_mid_inference(client, payload):
    queue.worker_function = suicidal_inference_worker
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 202
    task_id = response.json()["task_id"]
        
    # Poll dynamically to heal test suite race conditions
    status_body = {}
    for _ in range(15):
        await asyncio.sleep(0.1)
        status_res = await client.get(f"/workloads/{task_id}", headers=AUTH_HEADERS)
        status_body = status_res.json()
        if status_body["status"] in ["completed", "failed"]:
            break
            
    assert status_body["status"] == "failed"
    
    # Check the standard Python error message string details or exception class keys
    err_msg = status_body["result"]["error"]
    assert "BrokenProcessPool" in err_msg or "TerminatedWorkerError" in err_msg or "terminated abruptly" in err_msg
        
@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "success-1919",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_19_successful_completion_with_metrics(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    task_id = response.json()["task_id"]
    await asyncio.sleep(0.5)
    status_res = await client.get(f"/workloads/{task_id}", headers=AUTH_HEADERS)
    body = status_res.json()
    assert body["status"] == "completed"
    assert "detected_objects_count" in body["result"]
    assert "metrics" in body["result"]

@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "cancel-2020",
        "model": "yolov8s",
        "input_uri": FOX_IMAGE,
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_20_workload_cancellation_lifecycle(client, payload):
    response = await client.post("/workloads", json=payload, headers=AUTH_HEADERS)
    task_id = response.json()["task_id"]
    
    # Send explicit cancellation DELETE target signal route
    cancel_res = await client.delete(f"/workloads/{task_id}", headers=AUTH_HEADERS)
    assert cancel_res.status_code == 202