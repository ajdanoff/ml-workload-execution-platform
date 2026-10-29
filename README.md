# Asynchronous ML Workload Processing Platform

Production-oriented asynchronous FastAPI platform for offloading long-running machine learning inference workloads — such as Ultralytics YOLOv8 and Qwen2.5-VL — to isolated worker processes. The platform separates fast client-facing API operations from background processing to avoid latency regressions in the request path.

---

## 🏗️ Architectural Testing Strategy

The validation suite is organised into four lifecycle groups, balancing fast feedback, integration coverage, resilience and release readiness.

### 1. Fast CI

**Objective:** provide fast feedback on every commit and pull request.

**Scope:**
- Request/response schema validation.
- Serialisation and deserialisation rules.
- Authentication and authorisation checks.
- Positive and negative API scenarios.
- Idempotency contract checks.
- Basic task lifecycle transitions.

**Requirements:** tests are isolated from real external systems and use in-memory fakes or lightweight stubs for queue, database, object storage and inference worker dependencies.

### 2. Nightly Integration

**Objective:** verify behaviour in a realistic multi-component environment.

**Scope:**
- API + PostgreSQL + message broker + object storage + worker integration.
- Concurrent workload creation and idempotency under parallel requests.
- Task lifecycle transitions across services.
- Migration and configuration compatibility checks.
- Regression scenarios on a fixed dataset.

**Requirements:** Docker Compose or testcontainers-based environment; scheduled runs to avoid slowing down pull-request pipelines.

### 3. Fault Injection

**Objective:** verify graceful degradation and recovery behaviour.

**Scope:**
- Message broker unavailability.
- Database connection failures.
- Object storage errors, including missing input artifacts.
- Worker crash or termination during task processing.
- Queue backlog, retries, dead-letter handling and task timeout behaviour.

**Expected behaviour:** no task remains permanently in `running`; failures are recorded with machine-readable error codes, metrics and logs; retry and recovery policies are applied where applicable.

### 4. Performance Gates

**Objective:** validate latency, throughput and resource behaviour before rollout.

**Scope:**
- API latency percentiles: p50, p95, p99.
- Throughput under expected and peak load.
- Worker queue depth and processing time.
- CPU, memory and GPU/NPU resource consumption.
- Rate-limiting behaviour and `Retry-After` semantics.

**Requirements:** performance tests run against a controlled environment with a fixed workload profile and are compared against agreed SLO baselines.

---

## 🤝 MLOps Contract Testing Framework

The MLOps pipeline enforces contracts across three layers to prevent incompatible changes from reaching production:

```text
[ MLOps Pipeline Levels ]
│
├──────────────────────────────┼──────────────────────────────┼──────────────────────────────┐
▼                              ▼                              ▼                              
1. Data Contracts              2. Model Artifact Contracts    3. Serving API Contracts       
(Pandera / Great Expectations) (MLflow Registry)              (OpenAPI / Pact / CDC)         
├── Feature schemas            ├── Input/output signatures    ├── JSON payload structures    
├── Types and required fields  ├── Tensor shapes and dtypes   ├── HTTP/gRPC status codes     
├── Value boundaries           ├── Serialisation and loading  ├── Machine-readable errors    
└── Data drift [alert]         └── Quality floor              └── Latency/SLO gate [canary]  
```

### 1. Data Contracts — Data-to-Pipeline Layer

**Feature schema compliance:** validates column names, data types, required fields, uniqueness constraints and allowed value ranges. Critical schema violations block pipeline execution.

**Data quality checks:** detect missing values, `NaN`/`Inf` values, invalid categorical values, duplicate keys and out-of-range numerical features.

**Data drift monitoring:** compares production or incoming data distributions with a training baseline. Moderate drift produces alerts and metrics for investigation or retraining; it does not necessarily block a release. Critical schema drift or severe quality degradation can block deployment.

### 2. Model Artifact Contracts — Pipeline-to-Registry Layer

**Model signature alignment:** verifies input and output names, tensor shapes, data types and expected batch dimensions.

**Serialisation integrity:** verifies that a model artifact — for example `.onnx`, `.pt` or TorchScript — can be loaded and executed by the target runtime, such as ONNX Runtime, TensorRT or Triton Inference Server.

**Quality floor verification:** evaluates a candidate model on a fixed golden dataset and compares key metrics — for example mAP, precision, recall, F1 or ROC-AUC — against a minimum threshold or the current production baseline.

### 3. Serving API Contracts — Model-Serving-to-Client Layer

**Schema validation:** OpenAPI/Pydantic validation with `extra="forbid"` prevents unknown fields and silent misconfiguration.

**Machine-readable errors:** all expected failures return a consistent error structure with a stable `code`, human-readable message, field details where applicable, and `trace_id`.

**Performance and SLO gates:** latency, throughput and error-rate thresholds are validated during performance testing and canary rollout. A violation can stop a rollout or trigger rollback.

---

## 🧪 Comprehensive Evaluation Matrix

| Group | # | Scenario | Input | Expected HTTP status | Verification | CI execution |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Fast CI** | 1 | Successful workload creation | Valid JSON payload | `202` | Response contains a valid `task_id` and `status="accepted"` | Commit |
| **Fast CI** | 2 | Optional parameters omitted | Payload without `params` | `202` | Default parameters are applied: `confidence=0.35`, `max_frames=1000` | Commit |
| **Fast CI** | 3 | Query existing workload status | Valid active `task_id` | `200` | Response contains a valid lifecycle status | Commit |
| **Fast CI** | 4 | Query non-existent task | Unknown `task_id` | `404` | Error contains `code="TASK_NOT_FOUND"` and `trace_id` | Commit |
| **Fast CI** | 5 | Missing required `model` field | Payload without `model` | `422` | Validation error identifies the `model` field | Commit |
| **Fast CI** | 6 | Unsupported model | `model="unknown"` | `422` | Error contains `code="MODEL_NOT_SUPPORTED"` | Commit |
| **Fast CI** | 7 | Invalid confidence value | `confidence=1.01` | `422` | Rejected before enqueueing; error identifies `params.confidence` | Commit |
| **Fast CI** | 8 | Invalid frame limit | `max_frames=0` | `422` | Rejected before enqueueing; positive integer required | Commit |
| **Fast CI** | 9 | Malformed input URI | `input_uri="not-a-uri"` | `422` | URI validation error returned | Commit |
| **Fast CI** | 10 | Repeated request with same idempotency key | Same payload and `idempotency_key` | `202` | Same original `task_id` returned; no duplicate task created | Commit |
| **Fast CI** | 11 | Same idempotency key, different payload | Same key, modified payload | `409` | Error contains `code="IDEMPOTENCY_KEY_CONFLICT"` | Commit |
| **Fast CI** | 13 | Request without authentication | No `Authorization` header | `401` | Request rejected before business logic | Commit |
| **Fast CI** | 14 | Insufficient permissions | Token without required scope | `403` | Error contains `code="INSUFFICIENT_PERMISSIONS"` | Commit |
| **Fast CI** | 17 | Missing input artifact | Valid payload, non-existent storage object | `202` then `failed` | Asynchronous worker marks task as failed with `code="INPUT_NOT_FOUND"` | Commit |
| **Fast CI** | 19 | Successful workload completion | Valid workload processed by worker | `200` for status request | Task status becomes `completed`; result metadata/artifacts are available | Commit |
| **Nightly** | 12 | Concurrent identical requests | Two identical requests with the same idempotency key | `202` | Both requests return the same `task_id`; exactly one task is created | Nightly |
| **Fault** | 16 | Message broker outage | Valid payload, broker unavailable | `503` | Error contains `code="BROKER_UNAVAILABLE"`; `broker_unavailable_total` metric is incremented | Fault injection |
| **Fault** | 18 | Worker crash during processing | Worker process terminated mid-task | Task leaves `running` | Retry policy is applied; after retries are exhausted, task becomes `failed`; no permanently hung task | Fault injection |
| **Performance** | 15 | Rate limit exceeded | Client exceeds request threshold | `429` | Response includes `Retry-After`; no task is created | Performance |
| **Fault / Lifecycle** | 20 | Workload cancellation | `DELETE /workloads/{task_id}` during processing | `202` | Cancellation accepted; task transitions to `cancelled`; worker does not publish a completed result | Fault injection |