import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock

# Import your FastAPI app instance and the global queue dependency
from src.app.main import app, queue

import pytest

import logging

logger = logging.getLogger()

@pytest.mark.positive
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-1111",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
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
        headers={"Authorization": "Bearer valid-token-admin"}
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
        "input_uri": "s3://bucket/video.mp4"
    }
])
async def test_case_2_omission_of_optional_params(client, payload):
    # Payload omitting the "params" key completely
    
    response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token"}
    )
    
    # Assertions
    assert response.status_code == 202
    body = response.json()
    assert "task_id" in body
    assert body["status"] == "accepted"

from src.app.services import db_store

@pytest.mark.positive
@pytest.mark.asyncio
@pytest.mark.parametrize("mock_task_id", [
    "test-task-uuid-abcde",
    "test-task-uuid-fghij"
])
async def test_case_3_query_existing_workload_status(client, mock_task_id):
    # 1. seed a mock workload state directly into our stateful DB store
    db_store[mock_task_id] = {
        "status": "running",
        "payload": {
            "model": "yolov8s",
            "input_uri": "s3://bucket/video.mp4"
        },
        "result": None
    }
    
    # 2. query the workload status using the GET routine
    response = await client.get(
        f"/workloads/{mock_task_id}",
        headers = {"Authorization": "Bearer valid-token"}
    )
    
    # 3. assertions
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == mock_task_id
    assert body["status"] == "running"
    assert body["result"] == None

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-4444",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_4_missing_required_model_field(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token-admin"}
    )
    
    # Assertions
    assert response.status_code == 422
    
    # Verify that response body explicitly highlights the validation error path
    body = response.json()
    assert "detail" in body
    assert body["detail"][0]["loc"] == ["body", "model"]
    assert body["detail"][0]["msg"] == "Field required"

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-5555",
        "model": "non-existent-vlm-model-999",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_5_unsupported_model_architecture_type(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token-admin"}
    )
    
    # Assertions
    assert response.status_code == 422
    
    # Verify that response body explicitly highlights the validation error path
    body = response.json()
    assert "detail" in body
    assert "model" in body["detail"][0]["loc"]

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-6666",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 1.01,
            "max_frames": 1000
        }
    }
])
async def test_case_6_upper_boundary_for_confidence_parameter(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token-admin"}
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
        "idempotency_key": "unique-key-7777",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 0
        }
    }
])
async def test_case_7_zero_value_boundary_for_max_frames(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token-admin"}
    )
    
    # Assertions
    assert response.status_code == 422
    
    # Verify that response body explicitly highlights the validation error path
    body = response.json()
    assert "detail" in body
    assert "max_frames" in body["detail"][0]["loc"]
    assert "greater than 0" in body["detail"][0]["msg"]

@pytest.mark.negative_and_boundary
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-8888",
        "model": "yolov8s",
        "input_uri": "invalid-http-uri-path/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_8_invalid_string_uri_format(client, payload):
    # Complete, compliant payload meeting all schema expectations
    
    # Send request with a valid token required by our dependency injection
    response = await client.post(
        "/workloads",
        json=payload,
        headers={"Authorization": "Bearer valid-token-admin"}
    )
    
    # Assertions
    assert response.status_code == 422
    
    # Verify that response body explicitly highlights the validation error path
    body = response.json()
    assert "detail" in body
    assert "input_uri" in body["detail"][0]["loc"]
    assert "String should match pattern" in body["detail"][0]["msg"]

@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "unique-key-9999",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_9_repeated_request_with_same_idempotency_key(client, payload):
    
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
    assert second_body["status"] == "accepted"

import asyncio

@pytest.mark.idempotent
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "race-condition-key-1010",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_10_concurrent_identical_requests(client, payload):
    
    # Fire both async client operations concurrently using asyncio gather
    task1 = client.post("/workloads", json=payload, headers={"Authorization": "Bearer valid-token-admin"})
    task2 = client.post("/workloads", json=payload, headers={"Authorization": "Bearer valid-token-admin"})
    
    response1, response2 = await asyncio.gather(task1, task2)
    
    assert response1.status_code == 202
    assert response2.status_code == 202
    
    body1 = response1.json()
    body2 = response2.json()
    
    assert body1["task_id"] == body2["task_id"]

@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "race-condition-key-1111",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_11_request_without_authentication_token(client, payload):
    response = await client.post(
        "/workloads",
        json=payload
    )
    
    assert response.status_code == 401
    body = response.json()
    assert "detail" in body
    assert body["detail"] == "Missing Authorization Header"

@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "auth-key-1212",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_12_request_without_authentication_token(client, payload):
    response = await client.post(
        "/workloads",
        json=payload,
        headers = {"Authorization": "Bearer invalid-expired-or-restricted-token"}
    )
    
    assert response.status_code == 403
    body = response.json()
    assert "detail" in body
    assert "Forbidden" in body["detail"]

from src.app.services import queue

@pytest.mark.authentication
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1313",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_13_unreachable_queue_infrastructure(client, payload):
    # 1. simulate an infrastructure outage
    queue.is_connected = False
    
    try:
        # 2. dispatch the request
        response = await client.post(
            "/workloads",
            json=payload,
            headers = {"Authorization": "Bearer valid-token"}
        )
        
        # 3. assertions
        assert response.status_code == 503
        body = response.json()
        assert "detail" in body
        assert "Queue Broker Offline" in body["detail"]
    
    finally:
        # 4. clean up state: restore broker connection status for subsequent tests
        queue.is_connected = True

from src.app.services import queue, broken_s3_inference_worker, suicidal_inference_worker

@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1414",
        "model": "yolov8s",
        "input_uri": "s3://bucket/missing-video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_14_missing_s3_object_storage_file_source(client, payload, mocker):
    
    # 1. re-route the queue behaviour to the serializable error worker
    queue.worker_function = broken_s3_inference_worker
        
    response = await client.post(
        "/workloads",
        json=payload,
        headers = {"Authorization": "Bearer valid-token"}
    )
        
    # 1. verify the initial HTTP transaction is successfully accepted
    assert response.status_code == 202
    task_id = response.json()["task_id"]
        
    # 2. allow the asynchronous event loop to execute the background worker task
    await asyncio.sleep(0.2)
        
    # 3. query the status endpoint to verify the state transition
    status_response = await client.get(
        f"/workloads/{task_id}",
        headers = {"Authorization": "Bearer valid-token"}
    )
        
    assert status_response.status_code == 200
    status_body = status_response.json()
        
    # verify the workload state successfully marked the infrastructure failure
    assert status_body["status"] == "failed"
    assert "S3 Object not found" in status_body["result"]["error"]
    
import os
import signal

@pytest.mark.infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {
        "idempotency_key": "infra-key-1515",
        "model": "yolov8s",
        "input_uri": "s3://bucket/video.mp4",
        "params": {
            "confidence": 0.35,
            "max_frames": 1000
        }
    }
])
async def test_case_15_worker_process_node_crashes_mid_inference(client, payload, mocker):
    
    # 1. re-route the queue behaviour to the process-killing worker
    queue.worker_function = suicidal_inference_worker
    
    response = await client.post(
        "/workloads",
        json=payload,
        headers = {"Authorization": "Bearer valid-token"}
    )
    
    assert response.status_code == 202
    task_id = response.json()["task_id"]
        
    # give the process pool executor time to handle the sudden termination of the child process
    await asyncio.sleep(0.3)
        
    # query the lookup endpoint to evaluate the internal error state logic
    status_response = await client.get(
        f"/workloads/{task_id}",
        headers = {"Authorization": "Bearer valid-token"}
    )
        
    assert status_response.status_code == 200
    status_body = status_response.json()
            
    # The application layer should catch the BrokenProcessPool exception
    # and accurately transition the workload to a failed state.
    assert status_body["status"] == "failed"
    assert "BrokenProcessPool" in status_body["result"]["error"] or "TerminatedWorkerError" in status_body["result"]["error"]
    