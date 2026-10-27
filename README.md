# ML Workload Execution Platform

An asynchronous FastAPI engine designed to handle heavy ML/Computer Vision inference pipelines (e.g., YOLOv8) without blocking incoming API routing loops.

## 🚀 Getting Started

### 1. Initialize Virtual Environment & Dependencies
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install fastapi[standard] pytest pytest-asyncio httpx pytest-mock
```

### 2. Launch Local Server
```bash
fastapi dev src/app/main.py
```

### 3. Run Asynchronous Test Suite
```bash
pytest
```

---

## 🧪 Test Cases Matrix

The platform incorporates a 15-case test suite to ensure robust handling across all positive execution loops, negative input states, and infrastructure failures:

| # | Scenario | Input | Expectation | Verification | CI |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Positive Scenarios** | | | | | |
| 1 | Successful workload creation | Valid JSON (all fields provided) | 202 Accepted | Response contains a valid `task_id` (UUIDv4 format) and `status="accepted"`. | **Yes** |
| 2 | Omission of optional `params` field | JSON payload missing the `"params"` block entirely | 202 Accepted | API handles default internal parameters safely; task is initialized. | **Yes** |
| 3 | Query existing workload status | `GET /workloads/{task_id}` for a valid active ID | 200 OK | Response contains matching `task_id` and an allowed enum status (e.g., `running`). | **Yes** |
| **Negative & Boundary Conditions** | | | | | |
| 4 | Missing required `model` field | JSON payload missing `"model"` key | 422 Unprocessable | Response body contains clean validation error context pinpointing the field. | **Yes** |
| 5 | Unsupported model architecture type | `"model": "non-existent-vlm-model-999"` | 422 Unprocessable | Request rejected by schema validator before spawning process/queue tasks. | **Yes** |
| 6 | Upper boundary for `confidence` parameter | `"confidence": 1.01` in params | 422 Unprocessable | Request rejected by schema validator before spawning process/queue tasks. | **Yes** |
| 7 | Zero value boundary for `max_frames` | `"max_frames": 0` or `-1` | 422 Unprocessable | Enforces positive integers for frame constraints via structural schema validation. | **Yes** |
| 8 | Invalid string URI format | `"input_uri": "not-a-valid-uri"` | 422 Unprocessable | Rejects input string that fails basic pattern validation rules for target storages. | **Yes** |
| **Idempotency** | | | | | |
| 9 | Repeated request with identical idempotency key | Identical payload and same `"idempotency_key"` | 202 Accepted | Returns the exact original `task_id` and does not spawn duplicate worker processing. | **Yes** |
| 10 | Concurrent identical requests | Two identical requests sent at the exact same millisecond | 202 Accepted | First call creates task; second call gracefully resolves to the same ID using safe async locking. | **Yes** |
| **Authorization** | | | | | |
| 11 | Request without Authentication Token | Valid JSON payload but missing `Authorization` header | 401 Unauthorized | Access blocked instantly before any routing logic triggers. | **Yes** |
| 12 | Token with expired or restricted scopes | Valid JSON payload but token lacks workload permissions | 403 Forbidden | Request blocked due to role/scope mismatch. | **Yes** |
| **Infrastructure & Downstream Failures** | | | | | |
| 13 | Unreachable Queue Infrastructure | Valid JSON payload but backing Broker (Redis/Rabbit) is offline | 503 Service Unavail. | API safely handles network timeouts from queue and does not drop task silently. | **Yes** |
| 14 | Missing S3 Object Storage File Source | Valid payload layout but target video URI does not exist in bucket | 202 then `failed` | Task starts as `accepted` but updates state asynchronously to `failed` upon lookup check. | **Yes** |
| 15 | Worker process node crashes mid-inference | Active workload running inside `ProcessPoolExecutor` gets `SIGKILL` | State: `failed` | Status transitions cleanly to `failed` with error metrics, avoiding a permanent hung state. | **Yes** |
