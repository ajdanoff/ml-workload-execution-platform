# Workload Pipeline Architecture, Retry Policies, & Fault-Tolerance Specification (v2)

Documenting the transactional processing flow, structural component topology, failure remediation rules, and atomicity controls for the asynchronous ML execution platform.

---

## 🏗️ Workload Transactional Outbox Pipeline

The platform architecture follows an asynchronous, non-blocking ingestion pattern utilizing the **Transactional Outbox Pattern**. This couples database mutations and message publication into a single atomic transaction, completely eliminating dual-write inconsistencies across distributed nodes.


```
[ CLIENT ]
    │
    │ 1. POST /workloads
    │    JSON payload + Idempotency-Key
    ▼
┌──────────────┐
│ FastAPI API  │
└──────┬───────┘
       │
       │ 2. Validate request (Pydantic)
       │ 3. Begin DB transaction
       ▼
┌───────────────────────────────┐
│ PostgreSQL / State Store      │
│                               │
│ - Check idempotency_key       │
│ - Insert workload             │
│ - status = queued             │
│ - Insert outbox event         │
│ - Commit transaction          │
└──────────────┬────────────────┘
               │
               │ 4. Return 202 Accepted
               │    { task_id, status: "queued" }
               ▼
        [ CLIENT ]

       ┌─────────────────────────────┐
       │ Outbox Publisher / Relay    │
       └──────────────┬──────────────┘
                      │
                      │ 5. Publish task event
                      ▼
             ┌─────────────────┐
             │ Message Broker  │
             │ RabbitMQ/Redis  │
             └────────┬────────┘
                      │
                      │ 6. Consumer receives task
                      ▼
             ┌─────────────────┐
             │ Inference Worker│
             └────────┬────────┘
                      │
                      │ 7. Update status: running
                      │ 8. Fetch input from S3/MinIO
                      │ 9. Run inference: YOLOv8 / Qwen2.5-VL
                      │ 10. Save artifacts and metrics
                      │ 11. Update status: completed / failed
                      ▼
             ┌─────────────────┐
             │ PostgreSQL      │
             │ + S3 / MinIO    │
             └────────┬────────┘
                      │
                      │ 12. Client polls status
                      ▼
        [ CLIENT ]
        GET /workloads/{task_id}
        → completed + artifact URL
```
### Component Topology & Responsibilities
1. **API Serving Layer (FastAPI):** Ingests incoming calls, validates schemas using Pydantic, passes the payload to an atomic transaction, and yields an immediate `202 Accepted` response.
2. **PostgreSQL Database State Store:** Acts as the cluster's persistent source of truth. Tracks workloads, manages task states, and contains the transactional outbox table.
3. **Outbox Publisher / Relay:** A detached, lightweight service tracking the database outbox records. It ensures **at-least-once delivery** to the broker.
4. **Message Broker (RabbitMQ Quorum Queues):** Built with durable queues and low prefetch limits to evenly distribute long-running tasks among available nodes.
5. **Inference Worker:** A horizontally scalable processing consumer executing within multi-process wrappers to prevent GPU/CPU isolation deadlocks.

---

## 🔒 Concurrent Idempotency Locking Mechanism

To guarantee that parallel concurrent requests striking the endpoint at the exact same millisecond with the identical `idempotency_key` create **exactly one task**, the platform relies on **PostgreSQL unique constraints as the source of truth**, optimized by a Redis distributed lock.

* **Tier 1: High-Concurrency Optimization (Redis Lock):** Before beginning the database transaction, the API attempts to acquire a short-lived lock in Redis (`SET lock:idempotency:{key} NX PX 5000`). This optimization step limits resource contention and eliminates unnecessary database transaction rollbacks.
* **Tier 2: Unique Database Invariants (PostgreSQL Unique Constraint):** The relational schema enforces a strict `UNIQUE` constraint on the `idempotency_key` column.
* **Conflict Resolution:** If two concurrent threads pass the Redis lock check simultaneously, they both hit the database write phase:
  * **The Winner:** The first transaction to commit successfully inserts the workload record with a status of `"queued"`, saves the outbox intent, and returns a `202 Accepted` with a new `task_id`.
  * **The Loser:** The trailing concurrent transaction hits a database `unique_violation` exception. The API catches this error, rolls back the uncommitted transaction, and queries the database for the active record matching that `idempotency_key`.
    * If the incoming payload parameters **perfectly match** the existing record's parameters, the API returns the original `task_id` with a `202 Accepted` status.
    * If the payload properties **differ**, it rejects the request instantly by returning an HTTP `409 IDEMPOTENCY_KEY_CONFLICT` error.

---

## 🔄 Fine-Tuned MLOps Retry Policies Matrix

The system maps out specific error handlers depending on the underlying failure class to prevent infinite retry loops or resource deadlock:

| Failure Type | Practical Examples | Execution Remediation Strategy | Retry Threshold | Backoff Delay Mechanics | Failure Escalation Path (Terminal Outcome) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Transient Broker Error** | Broker connection lost, publish timeout | **Automatic Async Retry** via Outbox Relay. | `5 Times` | **Exponential Backoff + Jitter:** 1s, 2s, 4s, 8s, 16s. | Raise high-priority monitoring alert. Task stays `queued` in DB; publisher retries later. |
| **Transient S3 Outage** | Storage network timeout, transient 5xx HTTP codes | **Automatic Worker Retry** during execution. | `3-5 Times` | **Exponential Backoff + Jitter**. | Transition task status to `failed`. Append machine-readable `INPUT_FETCH_FAILED` code. |
| **Missing Input Source** | `NoSuchKey` exception, malformed input URL. | **Immediate Failure Interception** (No Retries). | `0 Times` | No Delay (Immediate Termination). | Transition task status to `failed`. Append machine-readable `INPUT_NOT_FOUND` code. |
| **Schema Validation Anomaly** | Missing `model` key, invalid bounds, unsupported models. | **Reject at API Boundary** (No Retries). | `0 Times` | No Delay (Immediate Interception). | Return `422 Unprocessable` or `400 Bad Request`. Task record is **not** created. |
| **Systemic Worker Crash** | Worker process killed unexpectedly, node lost. | **Late ACK Broker Redelivery**. | `2-3 Times` | **Exponential Backoff + Jitter**. | Transition task status to `failed`. Append machine-readable `WORKER_CRASHED` code. |
| **GPU / Out-Of-Memory Outage** | Out-of-memory error (`CudaOutOfMemory`), OOM killer trigger. | **Conditional Guarded Retry** (Do not retry blindly). | `1-2 Times` | Delay + resource usage guard checks. | Retry only if dynamic configuration allows lowering batch sizes/memory load; else `failed` with code `GPU_OOM`. |
| **Rate Limit / Resource Throttle** | Client request spikes exceeding threshold limitations. | **Client-Driven Retries**. | Driven by client. | Throttled by server response `Retry-After` header value. | Drop request before queue ingestion. Request does not generate a task record. |
| **Workload Cancellation** | User manually stops task via `DELETE /workloads/{id}`. | **Cancellation Signal Processing**. | `0 Times` | No Delay (Immediate Interception). | Transition task status to `cancelled`. Inference worker ignores or drops the running context. |

---

## 🛠️ Worker SIGKILL Crash Resilience & Watchdog Sweeps

A `SIGKILL` (`kill -9` or an infrastructure `OOMKilled` event) immediately halts a worker process at the operating system level, giving Python no opportunity to execute local graceful cleanup hooks or catch exceptions. The system handles this using **Broker-Level Late Acknowledgements** paired with **Task-Level Checkpoint and Watchdog Auditing**:

### 1. Broker-Level Late Acknowledgements
The platform configures the message broker (RabbitMQ) to use **manual acknowledgements**. When a worker pulls a task from the queue, it does **not** send an immediate acknowledgement (`ACK`). The broker keeps the message in an unacknowledged state, hiding it from other active consumers.

### 2. Automated Re-queuing
The moment the worker process receives a `SIGKILL`, its TCP network connection to RabbitMQ drops instantly. The broker detects the dead consumer channel and **automatically returns the unacknowledged task to the queue** (`re-queue`), allowing healthy worker nodes to pick it up.

### 3. Task-Level Recovery Logic
When a fresh worker consumes the re-queued message, it inspects the database record:
* **With Checkpoints:** If the workload supports incremental checkpoints, the worker reads the last successfully processed frame/layer index from PostgreSQL and resumes execution mid-stream.
* **Without Checkpoints:** The worker restarts the model inference sequence from scratch. The task's internal hardware retry counter is incremented. If the counter exceeds the allowed limits, the task status transitions to `failed` with the error code `WORKER_CRASHED`.

### 4. Watchdog / Lease Resolution Hooks
If a worker drops its connection *after* sending the task acknowledgement but *before* committing the final inference output to PostgreSQL, the task would remain stuck as `status="running"` indefinitely. To prevent this deadlock, the worker periodically updates a `heartbeat` or lease field in the database while running. 

A detached **Watchdog Service** performs regular sweeps of the database. If it detects a task in a `running` state whose heartbeat has not been updated within a specified timeout, it flags the task as `stalled`, coordinates with the broker to verify consumer status, and triggers retry mechanisms or shifts the status to `failed` with a machine-readable `WORKER_CRASHED` error code.