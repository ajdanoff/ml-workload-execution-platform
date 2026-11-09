# src/app/clickhouse_init.py
import clickhouse_connect
import os
import logging

logger = logging.getLogger(__name__)

def init_clickhouse():
    # Constructing connection mapping targets dynamically matching compose hostnames
    client = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "clickhouse"),
        port=int(os.getenv("CLICKHOUSE_PORT", 8123)),
        username=os.getenv("CLICKHOUSE_USER", "ch_admin"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "ch_secure_password"),
        database=os.getenv("CLICKHOUSE_DB", "ml_telemetry_db")
    )
    
    logger.info("Declaring ClickHouse analytical log architectures...")
    
    # ➔ Table A: Inference Execution Footprint Metrics
    client.command("""
        CREATE TABLE IF NOT EXISTS inference_logs (
            task_id String,
            model_name LowCardinality(String),
            latency_ms Float32,
            objects_detected UInt16,
            status Enum('success' = 1, 'failed' = 2),
            timestamp DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (model_name, timestamp);
    """)
    
    logger.info("ClickHouse schemas successfully established.")
