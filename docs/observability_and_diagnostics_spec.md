# Operational MLOps Observability, Alerting & Incident Diagnostics Specification

Documenting core signal metrics, dashboard configurations, multi-container failure playbooks, and service-level agreement (SLA) alerts for the asynchronous ML execution platform.

---

## 📊 1. Architectural Metric Matrix (Signal-Based Taxonomy)

The platform categorizes metrics based on **User-Facing** and **Operational** signals: *Availability, Latency, Throughput, Saturation, and Correctness*, completely avoiding fragmented component tracking.

### API Ingestion Layer
*   **Throughput & Availability:** `http_requests_total{route="/workloads", method="POST", status=~"2xx|4xx|5xx"}` (Counter)
    *   *Role:* Measures ingress rate and monitors availability/error ratios per route.
*   **Latency Boundaries:** `http_request_duration_seconds{route="/workloads", method="POST"}` (Histogram)
    *   *Role:* Measures p50/p95/p99 entrypoint latencies before task creation.
*   **Correctness Safeguards:** `http_requests_total{route="/workloads", status="409"}` (Counter)
    *   *Role:* Monitors payload conflict rates and key reuse anomalies.

### Inference Worker Layer
*   **Saturation & Progress:** `ml_worker_heartbeat_timestamp{instance="..."}` (Gauge)
    *   *Role:* Evaluated via `time() - ml_worker_heartbeat_timestamp` to determine worker liveness.
*   **Latency Boundaries:** `ml_inference_duration_seconds{model="...", batch_size="...", status="..."}` (Histogram)
    *   *Role:* Tracks ML processing time across different models and load configurations.
*   **Correctness Safeguards:** `worker_task_retries_total` & `worker_dlq_messages_total` (Counters)
    *   *Role:* Tracks background processing retry exhaustion and DLQ offloading rates.

### Message Broker (RabbitMQ Layer)
*   **Throughput:** `rabbitmq_queue_messages_ready{queue="ml_workloads_queue"}` (Gauge)
    *   *Role:* Measures backlog depth (messages waiting for a consumer).
*   **Saturation:** `rabbitmq_queue_messages_unacknowledged{queue="ml_workloads_queue"}` (Gauge)
    *   *Role:* Tracks pulled messages currently held in memory under manual ACK locks.
*   **SLA Latency:** `rabbitmq_queue_oldest_message_age_seconds{queue="ml_workloads_queue"}` (Gauge)
    *   *Role:* Measures queue wait time for the oldest unconsumed message.

### State Store (PostgreSQL Layer)
*   **Saturation:** `pg_stat_activity_long_transactions` (Gauge)
    *   *Role:* Identifies open transactions running for over 10 seconds to protect migrations from blocking.
*   **Throughput & Backlog:** `pg_outbox_unprocessed_records` & `pg_outbox_oldest_event_age_seconds` (Gauges)
    *   *Role:* Tracks outbox relay lag and detects failure states in the outbox publisher thread.

### Object Storage (S3 / MinIO Layer)
*   **Latency Boundaries:** `s3_request_duration_seconds{operation=~"GetObject|PutObject"}` (Histogram)
    *   *Role:* Measures artifact download/upload times to isolate storage network limits.
*   **Correctness & Saturation:** `s3_request_errors_total{code=~"NoSuchKey|429Throttling|5xx"}` (Counter)
    *   *Role:* Tracks missing input assets, AWS/MinIO service errors, or rate throttling.

---

## 🎛️ 2. Grafana Worker Dashboard Configuration Blueprint

The dashboard answers four questions: *Are the workers alive? Are they completing tasks? Is the queue backlog growing? Are resource limits being reached?*

```
┌──────────────────────────────────────────────────────────────┐
│ WORKER HEALTH                                                │
│ [Active workers] [Heartbeat age] [Restarts] [OOM kills]      │
│                                                              │
│ TASK PROCESSING                                              │
│ [Task rate] [Success rate] [Retries] [DLQ rate]              │
│ [Task duration p50/p95/p99 by model]                         │
│                                                              │
│ QUEUE PRESSURE                                               │
│ [Ready messages] [Unacked messages] [Oldest message age]     │
│ [Consumer count]                                             │
│                                                              │
│ RESOURCE SATURATION                                          │
│ [CPU] [Memory] [GPU utilization] [GPU memory]                │
└──────────────────────────────────────────────────────────────┘
```

### Threshold & Operational Alerting Rules

| Panel Target | Prometheus Query Expression | Warning | Critical |
| :--- | :--- | :--- | :--- |
| **Heartbeat Age** | `time() - max(ml_worker_heartbeat_timestamp) by (instance)` | `> 2 × interval` (60s) | `> 3 × interval` (90s) |
| **Oldest Message** | `rabbitmq_queue_oldest_message_age_seconds{queue="ml_workloads_queue"}` | `> 300` (5 mins) | `> 900` (15 mins) |
| **DLQ Ingestion** | `increase(worker_dlq_messages_total[10m])` | `> 0` | Continuous growth slope over 15 mins |
| **Task Processing** | `histogram_quantile(0.95, sum(rate(ml_inference_duration_seconds_bucket[5m])) by (le, model))` | `> 1.2 × SLO` | `> 1.5 × SLO` |
| **GPU VRAM Load** | `worker_gpu_memory_used_bytes / worker_gpu_memory_limit_bytes` | `> 0.85` (85%) | `> 0.95` (95%) |
| **Container Restarts**| `increase(kube_pod_container_status_restarts_total{container="worker"}[15m])` | `> 0` | Re-occurring restart patterns |

---

## 🔍 3. Incident Diagnostics: Tasks Stuck in `running` Status

### Incident Scenario
Tasks are stuck with `status = "running"`, the active RabbitMQ queue is empty, the Dead Letter Queue (DLQ) is empty, and unhandled exception error logs are zero.

### Diagnostic Workflow Playbook

```
[ INGESTION TASK DIAGNOSTIC TREE ]
│
┌───────────────────────────────────────┼───────────────────────────────────────┐
▼                                       ▼                                       ▼
1. EVALUATE CLUSTER LOAD               2. TRACE TASK ID RECORD                 3. ENGINE AUDITS
├── Total Running Counters              ├── Find last active trace span         ├── pg_stat_activity locks
├── Config drift / Release checks       ├── Check Object artifact footprint     ├── TCP stream timeouts
└── Pod exit code 137 validation        └── Verify manual ACK completion status └── Watchdog service state
```
1.  **Determine Scale & Scope:**
    *   Query the total count of running tasks. Isolate whether this affects *all* workers and models, or is restricted to a single model variant (e.g., Qwen2.5-VL only). Check your CI release timeline logs for recent configuration updates or active deployments.
2.  **Audit the Container Runstates (Look for Silent Suicide):**
    *   Execute `kubectl get pods -o wide` or `docker compose ps` to inspect container uptimes.
    *   Check for an **exit code 137 (`OOMKilled`)** or container restarts. Run `dmesg -T | grep -i oom` or inspect cluster node events to determine if the operating system's OOM-killer abruptly shut down the worker process due to a CUDA memory spike before it could log an error.
3.  **Cross-Reference the Broker with the Database:**
    *   If `ready = 0` and `unacked = 0` in the queue, but tasks are marked as `running` in PostgreSQL: The worker pulled the message and processed it, but crashed or blocked *after* sending the RabbitMQ acknowledgement (`ACK`) but *before* committing the updated `completed` state to PostgreSQL.
4.  **Isolate a Target `task_id` Trace:**
    *   Extract a stuck `task_id` and query your log aggregation system (Elastic/Loki) for its full `trace_id` span history.
    *   Trace the lifecycle timeline: `download ➔ inference ➔ upload ➔ DB update ➔ ACK`. Identify the last successful step to locate where the execution loop stalled.
5.  **Audit PostgreSQL Locks:**
    *   Query `pg_stat_activity` looking for queries stuck in states like `idle in transaction` or `wait_event_type="Lock"`.
    *   Run catalog lock tracing scripts to check if the worker's update query is deadlocked behind an unclosed database transaction:
        ```sql
        SELECT blocked_locks.pid AS blocked_pid, blocking_locks.pid AS blocking_pid 
        FROM pg_catalog.pg_locks blocked_locks ...
        ```
6.  **Verify Storage Artifact Completeness:**
    *   Check the target S3/MinIO bucket path. If the output artifact file exists but the database status is still stuck at `running`, the worker completed the computation but is blocked from updating the database state store.

### Root Cause Remediation Mapping

| Suspected Cause | Evidence Indicator | Remediation Action |
| :--- | :--- | :--- |
| **Worker OOM / SIGKILL** | Exit code 137, kernel OOM log trace, uncompleted task loops. | Reduce model batch sizes, scale up container memory limits, or roll back recent model weight weights changes. |
| **Database Transaction Deadlock** | Long-running queries, table locks inside `pg_locks` catalogs. | Terminate the blocking transaction backend PID via `SELECT pg_terminate_backend(pid)`. |
| **Hanging Network Stream** | Trace stops mid-download/upload; thread is alive but unresponsive. | Ensure all downstream storage connection configurations use explicit connection and read timeouts. |
| **Hanging Watchdog/Reaper Loop** | Tasks stay stuck in running status indefinitely past their TTL window. | Fix task-level lease logic. Implement a watchdog that automatically flags tasks as `failed` with code `WORKER_CRASHED` if their heartbeat lease expires. |

---

## ❓ 4. Metric Value Analysis: Queue Depth vs. Oldest Message Age

*   **Queue Depth** (`rabbitmq_queue_messages_ready`) answers *how much work is waiting*.
*   **Age of Oldest Message** (`rabbitmq_queue_oldest_message_age_seconds`) answers *how long the most delayed consumer has been waiting*.

For strict Service Level Agreement (SLA) enforcement, **Age of Oldest Message is the superior signal**. If a single task payload becomes corrupted or causes an edge-case infinite loop, a worker thread can stall indefinitely. In this scenario, your Queue Depth remains low (e.g., 1 message waiting), which won't trip any capacity or deep backlog alerts. However, that specific customer's request is stuck forever. Monitoring the oldest message age catches this processing bottleneck instantly, regardless of queue length.

### Production Prometheus Alert Rule Specifications

```yaml
groups:
  - name: ml-platform-sla-safety-gates
    rules:
      # Alert 1: Catches queue processing delays and consumer starvation (Ready Backlog)
      - alert: MLWorkloadsQueueAgeSLA
        expr: rabbitmq_queue_oldest_message_age_seconds{queue="ml_workloads_queue"} > 300
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Oldest ML workload message exceeds 5 minutes"
          description: >-
            The oldest message in ml_workloads_queue has been unconsumed for more than 5 minutes. 
            This indicates worker starvation, a stalled consumer pool, or a downstream pipeline bottleneck.

      # Alert 2: Catches in-flight tasks currently hung inside isolated processing nodes (Unacked Backlog)
      - alert: MLWorkloadsUnackedTaskAge
        expr: rabbitmq_queue_unacked_message_age_seconds{queue="ml_workloads_queue"} > 600
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Worker holds an unacknowledged task too long"
          description: >-
            A worker has held an unacknowledged ML workload message for more than
            10 minutes. This may indicate a hung inference process, blocked DB
            update, or missing task timeout.
```
