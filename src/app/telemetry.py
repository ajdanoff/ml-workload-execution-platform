import asyncio
import logging
import os
import clickhouse_connect

logger = logging.getLogger(__name__)

# Fetch connection credentials dynamically from environment coordinates
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", 8123))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "ch_admin")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "ch_secure_password")
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "ml_telemetry_db")

# Thread-safe global client cache pointer
_ch_client = None

def get_clickhouse_client():
    """Thread-safe instantiation of the high-speed columnar connection pool."""
    global _ch_client
    if _ch_client is None:
        try:
            _ch_client = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
                database=CLICKHOUSE_DB
            )
            logger.info("Successfully established thread-safe connection to ClickHouse server cluster.")
        except Exception as err:
            logger.error(f"Failed to connect to ClickHouse catalog engine: {str(err)}")
            raise err
    return _ch_client

def _write_log_sync(task_id: str, model_name: str, latency_ms: float, objects_detected: int, status: str):
    """Synchronous writer executed inside an isolated thread executor pool context."""
    client = get_clickhouse_client()
    
    # Structural row mapping matching our MergeTree columnar engine definition
    row = [task_id, model_name, latency_ms, objects_detected, status]
    
    # Batch insertion wrapper optimized for columnar processing speed
    client.insert(
        table='inference_logs',
        data=[row],
        column_names=['task_id', 'model_name', 'latency_ms', 'objects_detected', 'status']
    )

async def log_inference_telemetry(task_id: str, model_name: str, latency_ms: float, objects_detected: int, status: str = "success"):
    """Asynchronous telemetry emitter called by Inference Worker consumer threads.
    
    Uses asyncio.to_thread to completely eliminate main-loop thread-blocking lag.
    """
    try:
        await asyncio.to_thread(
            _write_log_sync,
            task_id,
            model_name,
            latency_ms,
            objects_detected,
            status
        )
        logger.info(f"Successfully shipped telemetry data for task {task_id} to ClickHouse cluster.")
    except Exception as err:
        # We catch exceptions softly and log them as errors rather than crashing the worker container.
        # Analytical logging layers should never brick your core execution pipeline.
        logger.error(f"Soft-catch telemetry fallback triggered. Failed to ship logs to ClickHouse: {str(err)}")
