# Zero-Downtime Database Migration & High-Concurrency Idempotency Specification

Documenting the database migration workflow, schema atomicity safeguards, application exception handling rules, and observability metrics for the asynchronous ML execution platform.

---

## 🏗️ 1. Zero-Downtime Data Migration Lifecycle

Enforcing a `UNIQUE` constraint on an active production table with legacy data or high concurrent traffic must strictly avoid full table locks, transactional deadlocks, and silent data destruction. 

```
[ PRODUCTION MIGRATION TIMELINE ]
│
[STEP 1: CODE REFACTOR] ──> Deploy FastAPI 202/409 Handling Engine (Live in Production)
│
▼
[STEP 2: DATA AUDIT]    ──> Quarantine Duplicates into Audit Table ➔ Purge from Primary
│
▼
[STEP 3: ASYNC INDEX]   ──> Build Unique Index CONCURRENTLY (Autocommit Block, Out of Tx)
│
▼
[STEP 4: ATTACH KEY]    ──> ALTER TABLE ... USING INDEX (Low-Traffic Window, Table Lock)
```
### Step 1.1: Refactoring the Application Layer (Deploy First)
Before activating the unique constraint in production, the application code must be deployed with the capability to catch unique violations gracefully. This prevents unhandled database exceptions from crashing user threads with `500 Internal Server Error` responses.

```python
# src/app/main.py (Inside create_workload route transaction block)
from fastapi import status, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

async def commit_workload_transaction(session, request):
    try:
        # Append the new workload configuration and its corresponding outbox event
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        orig = exc.orig
        
        # Catch PostgreSQL strict 23505 unique_violation error code safely across drivers
        if getattr(orig, "pgcode", None) == "23505":
            # Resolve intent: query the canonical record from the source-of-truth database
            stmt = select(Workload).where(Workload.idempotency_key == request.idempotency_key)
            result = await session.execute(stmt)
            existing_workload = result.scalar_one_or_none()
            
            if existing_workload:
                # Scenario A: Payload properties match exactly -> return the original task_id safely
                if (existing_workload.model == request.model and 
                    str(existing_workload.input_uri) == str(request.input_uri)):
                    return {"task_id": existing_workload.task_id, "status": existing_workload.status}
                
                # Scenario B: Key reuse attempt with different payload parameters -> trigger conflict error
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_KEY_CONFLICT",
                        "message": "This idempotency key has already been used for a different workload configuration."
                    }
                )
        raise exc
```

### Step 1.2: The Production Alembic Manifest
This script utilizes `op.get_context().autocommit_block()` to step completely out of implicit transaction scopes, protects canonical data using structural `MIN(id)` definitions, compares explicit Primary Keys during deletions, and leaves quarantine data untouched during downgrades to preserve an audit trail.

```python
"""add idempotency constraint safely

Revision ID: 12345_add_idempotency_constraint
Revises: previous_revision_id
Create Date: 2026-11-02 11:10:00
"""
from alembic import op
import sqlalchemy as sa

revision = "12345_add_idempotency_constraint"
down_revision = "previous_revision_id"

TABLE = "workloads"
QUARANTINE_TABLE = "workloads_idempotency_duplicates"
INDEX_NAME = "idx_workloads_idempotency_key_unique"
CONSTRAINT_NAME = "workloads_idempotency_key_unique"


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. DATA AUDIT & QUARANTINE PHASE
    # -----------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} AS
        SELECT w.*
        FROM {TABLE} w
        JOIN (
            SELECT idempotency_key, MIN(id) AS keep_id
            FROM {TABLE}
            GROUP BY idempotency_key
            HAVING COUNT(*) > 1
        ) d
          ON w.idempotency_key = d.idempotency_key
         AND w.id <> d.keep_id;
        """
    )

    # Cleanly remove the non-canonical duplicates comparing explicit Primary Keys
    op.execute(
        f"""
        DELETE FROM {TABLE} w
        USING {QUARANTINE_TABLE} q
        WHERE w.id = q.id;
        """
    )

    # -----------------------------------------------------------------
    # 2. BACKGROUND UNIQUE INDEX CREATION (CONCURRENTLY)
    # -----------------------------------------------------------------
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
            {INDEX_NAME}
            ON {TABLE} (idempotency_key);
            """
        )

    # -----------------------------------------------------------------
    # 3. FAIL-FAST INVALID INDEX VERIFICATION
    # -----------------------------------------------------------------
    invalid = op.execute(
        f"""
        SELECT 1
        FROM pg_index
        WHERE indexrelid = '{INDEX_NAME}'::regclass
          AND NOT indisvalid;
        """
    ).scalar()

    if invalid:
        raise RuntimeError(
            f"Index {INDEX_NAME} built as INVALID. "
            "Drop it concurrently, resolve underlying issues, and re-run migration."
        )

    # -----------------------------------------------------------------
    # 4. ATTACH UNIQUE CONSTRAINT USING INDEX
    # -----------------------------------------------------------------
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        ADD CONSTRAINT {CONSTRAINT_NAME}
        UNIQUE USING INDEX {INDEX_NAME};
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME};
        """
    )

    with op.get_context().autocommit_block():
        op.execute(
            f"""
            DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME};
            """
        )

    # CRITICAL: Do NOT drop the quarantine table during downgrade to preserve the audit trail.
```

---

## 🛠️ 2. Post-Failure Self-Healing Protocol (`INVALID` Indexes)

If `CREATE UNIQUE INDEX CONCURRENTLY` drops out mid-execution (due to client timeouts, active long-running lock contention, or network drops), it leaves an unusable `INVALID` catalog entry behind.

1. **Audit & Detect:** Query PostgreSQL's system catalogs to verify index status:
   ```sql
   SELECT indexrelid::regclass AS invalid_index_name
   FROM pg_index
   WHERE NOT indisvalid AND indexrelid::regclass::text = 'idx_workloads_idempotency_key_unique';
   ```
2. **Concurrently Purge:** You **cannot** simply rebuild or overlay a new index path. Run the teardown explicitly out of transaction blocks:
   ```sql
   DROP INDEX CONCURRENTLY IF EXISTS idx_workloads_idempotency_key_unique;
   ```
3. **Remediate Blockers:** Audit active runtime processes for long-running open queries or uncommitted transactions that prevent the index build from completing its verification phases. Clean up the channels and re-trigger Step 2 of the migration.

---

## 🔑 3. Same Key + Same Payload Resolution Contract

Returning an HTTP `202 Accepted` status code along with the existing `task_id` for an identical payload retry fulfills the core requirements of API Idempotency:

* **Handling Network Drops:** If a client drops their connection right after our server accepts a workload but before receiving the HTTP response, the client will naturally re-submit the identical payload.
* **Why 409 is Incorrect:** A `409 Conflict` tells the caller that the request cannot be processed due to a structural state mismatch. This forces the client application to throw an error even though the task is running successfully.
* **The 202 Replay Fix:** Returning `202` alongside the active `task_id` gracefully signals that the workload is safely registered. The client can seamlessly transition to tracking progress via `GET /workloads/{task_id}` without interrupting the pipeline.

---

## 📊 4. Operational MLOps Metric & Alerting Matrix

Runtime metrics differentiate between normal concurrent behavior (where our unique constraints catch race conditions) and genuine system errors (such as unhandled `500 Internal Server Error` responses).

| Metric | Type | Target Meaning | Alerting Threshold Trigger |
| :--- | :--- | :--- | :--- |
| `migration_invalid_indexes` | Gauge | Count of `INVALID` indexes left behind by failed background migrations. | **Critical:** Trigger immediate PagerDuty notification if count > 0. |
| `migration_quarantine_rows_total` | Counter | Total volume of historical duplicates moved to the audit table. | **Warning:** Alert if numbers rise unexpectedly during deployment. |
| `http_requests_total{status="202", idempotency_replay="true"}` | Counter | Volume of incoming client retries successfully matched to an existing task. | **Info:** Graph on Grafana dashboard to track network dropped connections. |
| `http_requests_total{status="409"}` | Counter | Volume of key reuse attempts with modified payloads. | **Warning:** Alert on sudden spikes, indicating an upstream client code bug. |
| `db_integrity_errors_total{constraint="idempotency_key"}` | Counter | Volume of unique violations (`23505`) reaching the database layer. | **Warning:** Alert **only** on anomalous spikes or if accompanied by `500` codes (indicates Redis lock is down). Moderate rates are expected behavior under load. |
| `migration_duration_seconds` | Histogram | Operational execution time duration of database migration steps. | **Warning:** Alert if execution duration exceeds the allocated maintenance window. |
| `migration_lock_wait_seconds` | Histogram | Wait time duration to acquire

# High-Concurrency Database Operations & Migration Quality Gateways

This specification outlines the technical safeguards, low-level transaction mechanics, validation suites, and nullability constraints governing data storage operations for the asynchronous ML execution platform.

---

## 🕒 1. Long-Running Transaction Audit & Lock Gates

Executing structural changes via `ALTER TABLE` requires acquiring a short-lived `ACCESS EXCLUSIVE` table lock. This statement will block behind *any* currently active query hitting that table. Consequently, it will build up a queue of all incoming application requests, causing immediate application-wide connection timeouts.

### Database Audit Query
Before running the final step of the database migration, the migration tool must query the internal system catalogs to detect background blockers:

```sql
SELECT 
    pid, 
    age(clock_timestamp(), query_start) AS duration, 
    state, 
    query
FROM pg_stat_activity
WHERE state != 'idle'
  AND age(clock_timestamp(), query_start) > INTERVAL '10 seconds'
  AND pid != pg_backend_pid();
```

### Safety Stop / Execution Gate
* **The Gate:** If the catalog query yields **any active row running for more than 10–30 seconds**, the migration execution must abort immediately to preserve live production availability.
* **Remediation:** If the long-running transaction is flagged as non-critical (e.g., a trailing analytical snapshot run or an unclosed report generation loop), terminate it cleanly before re-trying the lock:
```sql
SELECT pg_cancel_backend(pid); -- Attempts a graceful transaction cancel
-- Fallback for hard locks: SELECT pg_terminate_backend(pid);
```

---

## 🔄 2. Multi-Phase Catalog Mechanics: Why `DROP INDEX CONCURRENTLY` Requires Autocommit

Just like its initialization counterpart, the `DROP INDEX CONCURRENTLY` statement **cannot execute within a standard transaction block (`BEGIN ... COMMIT`)** and will fail fast if attempted.

### The Architectural Reason
Dropping or creating an index concurrently is fundamentally a multi-stage operation that spans across distinct database transaction milestones:
1. PostgreSQL updates the internal system catalogs to mark the index as dead (or invalid).
2. The core system drops active indexing visibility and **must wait for all active concurrent transactions to finish** to ensure no execution threads are currently using that index structure for query planning.
3. A final catalog swipe clears the physical storage mapping allocations.

If this multi-phase sequence were wrapped inside a standard transaction block, the command would be unable to wait for trailing concurrent sessions to finish without locking the system catalogs, creating an immediate database transaction deadlock. It requires an un-wrapped `autocommit` or an isolated session context to execute safely.

---

## 🧪 3. Mandatory CI Migration Test Scenarios

To safely validate this data migration path inside an automated `docker-compose` testing sandbox, the pipeline must execute three specific integration tests:

1. **The Quarantine & Deduplication Scan:** Seed the database table with explicit duplicate `idempotency_key` rows before running the migration. Execute the migration and assert that exactly **one canonical row (with the lowest ID)** remains in the primary table, and that all pruned rows are accurately written to the `workloads_idempotency_duplicates` audit table.
2. **The High-Concurrency Race Condition Check:** Launch parallel concurrent requests striking the API at the exact same millisecond with matching payloads using `asyncio.gather`. Verify that the database unique constraint correctly throws an internal `23505` error code, and that the API handles it gracefully, returning a `202` and a matching `task_id` for both calls without generating duplicate rows.
3. **The Payload Conflict Check:** Submit a request with a specific key, then immediately send a second request with the same key but a modified `input_uri` parameter configuration. Verify that the unique constraint interceptor catches the mismatch and correctly rejects it by throwing an HTTP `409 IDEMPOTENCY_KEY_CONFLICT` error.

---

## 🎛️ 4. Handling Column Nullability within Unique Constraints

If the `idempotency_key` is configured as a `nullable` type, you **can still build a unique index directly** over the column layout. 

### PostgreSQL `NULL` Treatment Mechanics
According to the SQL standard, **PostgreSQL treats `NULL` values as completely distinct, independent entities** within unique indexes. This means a unique constraint will **not** block duplicate null fields; you can insert an infinite number of rows where the `idempotency_key` is `NULL` without ever triggering a `23505 UniqueViolationError`. 

### Impact on Architecture
If your business logic expects a missing key to be treated as a validation failure rather than a unique task entry, you should add a partial unique index mapping tool parameter constraint: `CREATE UNIQUE INDEX ... ON workloads (idempotency_key) WHERE idempotency_key IS NOT NULL;` or enforce structural `NOT NULL` schema requirements directly at the database column definition boundary.
