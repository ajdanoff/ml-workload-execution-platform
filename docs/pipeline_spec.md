# Workload Pipeline Architecture, Retry Policies, & Fault-Tolerance Specification

Documenting the processing flow, architectural component topology, failure remediation rules, and atomicity controls for the asynchronous ML execution platform.

---

## 🏗️ Workload Pipeline Architecture

The platform architecture follows an asynchronous, non-blocking ingestion pattern to isolate user-facing API routers from heavy, CPU/GPU-bound machine learning execution matrices.


```
 [ CLIENT ]
     │  1. HTTP POST /workloads (JSON Payload + Idempotency Key)
     ▼
 ┌──────────┐      2. Write In-Flight Lock / Idempotency Check
 │  API     │ ───────────────────────────────────────────────┐
 │ (FastAPI)│ ◀───────────────────────────────────────────┐  │
 └────┬─────┘      3. Return 202 Accepted (Task ID)       │  │
      │                                                   │  │
      │ 4. Push Task (Task ID + Parameters)               │  │
      ▼                                                   │  ▼
 ┌──────────┐                                             │ ┌──────────────┐
 │ MESSAGE  │                                             │ │ DATABASE     │
 │ BROKER   │                                             │ │ (PostgreSQL/ │
 │ (Redis/  │                                             │ │  Redis State)│
 │ RabbitMQ)│                                             │ └──────────────┘
 └────┬─────┘                                             │        ▲
      │                                                   │        │
      │ 5. Pull Next Workload Task (FIFO Queue)           │        │
      ▼                                                   │        │
 ┌──────────┐                                             │        │
 │ INFERENCE│ 6. Async Storage Fetch (S3 Pre-check)       │        │
 │ WORKER   │ ───────────────────────────────────────┐    │        │
 │ (Python  │                                        ▼    │        │
 │ Multi-   │                              ┌───────────┐  │        │
 │ process) │                              │ CLOUD S3  │  │        │
 └────┬─────┘                              │ (Storage) │  │        │
      │                                    └─────┬─────┘  │        │
      │ 7. ML Model Processing Sequence          │        │        │
      │    (CUDA / PyTorch Bounding Boxes)       │        │        │
      │    ◀─────────────────────────────────────┘        │        │
      │                                                   │        │
      │ 8. Push Execution State & Metric Footprint        │        │
      └───────────────────────────────────────────────────┼────────┘
                                                          ▼
                                            [ CLIENT GET /workloads/{id} ]
                                              ➔ Returns "completed" + S3 URL
```

### Component Breakdown
1. **API (FastAPI Serving Layer):** Accepts the JSON workload configuration, runs strict schema verification, ensures atomicity using an asynchronous idempotency lock, and yields an immediate `202 Accepted` response.
2. **Database State Store:** Maintains systemic task lifecycle realities (`queued`, `running`, `completed`, `failed`, `cancelled`) along with calculated metric artifacts.
3. **Message Broker:** Acts as a persistent, highly available FIFO buffer separating ingestion spikes from bounded hardware compute resources.
4. **Inference Worker:** A decoupled processing worker executing inside a multi-process isolation context. It evaluates file availability, pulls network weights, handles memory pools, and streams computation results back to the database state store.

---

## 🔒 Concurrent Idempotency Locking Mechanism

To guarantee that concurrent requests striking the API at the exact same millisecond with the identical `idempotency_key` create **exactly one task**, the platform utilizes a coordinated two-tier distributed lock strategy:

### Tier 1: High-Concurrency Distributed Locking (Redis NX)
* **Atomic Acquisition:** Upon receiving a request, the API immediately attempts to set a short-lived key in Redis using atomic operations: `SET lock:idempotency:{key} {request_uuid} NX PX 5000` (Set if Not Exists, with a 5-second TTL).
* **The Winner:** The first concurrent request that successfully creates the lock passes through to the core business logic (generates the `task_id`, persists the baseline state record, and enqueues the payload).
* **The Loser(s):** Parallel matching requests that fail to acquire the lock do not crash or throw errors. Instead, they enter a brief async spin-lock polling routine (e.g., retrying every 50ms) to await the winner's completion.

### Tier 2: Relational Schema Safeguard & Lock Release
* **Unique Constraints:** The primary relational database enforces a strict `UNIQUE` constraint on the `idempotency_key` column.
* **Resolution Resolution:** Once the winning task is successfully enqueued and written to the database, it registers the final `task_id` against that `idempotency_key` in the state cache and releases the Redis lock. 
* **Graceful Exit:** The polling requests pick up the newly written database cache mapping, bypass duplicate queue scheduling entirely, and immediately return the original `202 Accepted` response containing the duplicate `task_id`.

---

## 🔄 Distributed Retry Policies Matrix

To build downstream resilience and prevent resource deadlock, the orchestration framework maps out specific error handlers depending on the underlying failure class:

| Target Failure Type | Error Signature Class | Execution Strategy | Retry Threshold | Backoff Delay Mechanics | Failure Escalation Path (Dead Letter Queue / Drop) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Transient Downstream Network Outage** | `S3NetworkConnectionError`, `BrokerTimeoutException` | **Automatic Async Retry** | `5 Times` | **Exponential Backoff** with jitter: $2^n \times 1\text{s}$ (e.g., 1s, 2s, 4s, 8s, 16s). | Move task payload to `Dead Letter Queue (DLQ)` for support analysis. Keep status as `failed`. |
| **Model Ingestion Error (Missing Source)** | `INPUT_NOT_FOUND`, `NoSuchKeyException` | **Immediate Fail** (No Retries) | `0 Times` | No Delay (Immediate Termination). | Drop immediately. Mark task state strictly to `failed`, log error code string to internal DB metadata. |
| **Hardware / Compute Infrastructure Break** | `BrokenProcessPool`, `TerminatedWorkerError`, `CudaOutOfMemory` | **Reschedule to Alternative Worker Node** | `2 Times` | **Fixed Delay:** Wait 5 seconds to let OS clear local memory cache contexts. | Shift task status to `failed`. Send automated alert pager metrics to infrastructure engineering. |
| **Data Validation Anomaly** | `ValidationError`, `MODEL_NOT_SUPPORTED` | **Reject at Entrypoint** (No Retries) | `0 Times` | No Delay (Immediate Interception). | Drop request instantly at the API boundary wrapper with a `422` status code. Never enters queue layers. |
| **Rate Limit / Resource Throttle** | `TooManyRequests`, `HTTP 429` | **Client-Driven Backoff** | Driven by client | Driven by client (respects `Retry-After` header value). | Drop request before queue ingestion if the client violates throttling guidelines. |

---

## 🛠️ Worker SIGKILL Crash Resilience & Watchdog Sweeps

Unlike a standard `SIGTERM` signal which triggers clean application shutdown hooks, a sudden `SIGKILL` (`kill -9` or an infrastructure Out-Of-Memory `OOMKilled` event) kills the worker process instantly at the OS boundary. The platform self-heals from these abrupt breaks through a combination of broker states and passive watchdog reapers:

### 1. Broker-Level Late Acknowledgements (At-Least-Once Delivery)
* **Visibility Timeout:** When an inference worker pulls a task payload from the Message Broker, it does **not** send an immediate acknowledgement (`ACK`). The task enters a locked, unacknowledged state hidden from other workers.
* **Auto-Requeuing:** If the active worker node receives a `SIGKILL`, its socket connection to the broker drops instantly. The broker detects the dead channel, automatically breaks the message lock, and puts the task back into the front of the FIFO queue (`re-queue`) to be processed by a healthy worker node.

### 2. Recovery via Retry and State Checkpoints
* **Context Restoration:** When a fresh worker node acquires the re-queued task, it evaluates the `task_id` state history. If the workload model logic supports incremental checkpoints, the worker reads the last valid frame metric signature from the database and resumes mid-stream. If checkpoints are unavailable, it resets execution from frame zero, incrementing the hardware failure retry tally counter.

### 3. The Watchdog / Reaper Sweep Service
* **Identifying Hanging Deadlocks:** Because a `SIGKILL` gives the dying process no time to write a final state update to the database, the affected record remains stuck as `status="running"` indefinitely.
* **Periodic Scans:** A detached cron-like **Watchdog Service** performs regular sweeps (e.g., every 60 seconds) querying the database store for items lingering in a `running` status past their calculated TTL or maximum execution time window.
* **State Clean and Metric Alarm:** The Watchdog service checks the broke