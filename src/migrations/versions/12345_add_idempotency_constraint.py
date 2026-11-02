"""add idempotency constraint safely

Revision ID: 12345_add_idempotency_constraint
Revises: previous_revision_id
Create Date: 2026-11-02 11:10:00
"""
from alembic import op
import sqlalchemy as sa

revision = "12345_add_idempotency_constraint"
down_revision = "previous_revision_id"
branch_labels = None
depends_on = None

TABLE = "workloads"
QUARANTINE_TABLE = "workloads_idempotency_duplicates"
INDEX_NAME = "idx_workloads_idempotency_key_unique"
CONSTRAINT_NAME = "workloads_idempotency_key_unique"


def upgrade() -> None:
    # -----------------------------------------------------------------
    # STEP 1: Quarantine duplicates, preserve canonical record via MIN(id)
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
    # STEP 2: Create unique index concurrently outside an active transaction
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
    # STEP 3: Fail fast if the concurrent background build left an INVALID index
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
    # STEP 4: Attach unique constraint using the prebuilt index
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
