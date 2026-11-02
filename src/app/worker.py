import asyncio
import json
import logging
import os
import signal
import aio_pika
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import update
from src.app.migrations import Workload, init_db_pool, async_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")
RMQ_URL = os.getenv("RABBITMQ_URL")

# src/app/worker.py (Inside process_task function)
async def process_task(payload: dict):
    task_id = payload.get("task_id") or payload.get("idempotency_key")
    uri = payload.get("input_uri", "")
    logger.info(f"Worker picked up Task ID: {task_id}. Evaluation parameters...")

    # Force database engine to align with the active processing loop
    init_db_pool()

    # ➔ Scenario A: Cross-container S3 Missing File Trigger Check
    if "missing-file" in str(uri):
        logger.warning(f"Chaos trigger detected: Simulating missing S3 storage for task {task_id}")
        async with async_session() as session:
            async with session.begin():
                from src.app.migrations import Workload
                stmt = update(Workload).where(Workload.task_id == task_id).values(
                    status="failed",
                    result={"error": "INPUT_NOT_FOUND: S3 Object not found in bucket"}
                )
                await session.execute(stmt)
        return

    # ➔ Scenario B: Cross-container Worker Process SIGKILL Crash Trigger Check
    if "suicidal-crash" in str(uri):
        crash_marker = "/tmp/worker_crashed_once"
        if not os.path.exists(crash_marker):
            logger.warning(f"Chaos trigger detected: Simulating brutal OS SIGKILL crash for task {task_id}")
            # Create a shared file marker. Since Docker Compose mounts named volumes or handles
            # restarts, we can write a local execution trace to coordinate single-shot suicides.
            with open(crash_marker, "w") as f:
                f.write("killed")
            os.kill(os.getpid(), signal.SIGKILL)
            return
        else:
            logger.info(f"Recovered redelivery sequence detected for task {task_id}. Proceeding with processing...")

    # Standard Successful Processing Path
    async with async_session() as session:
        async with session.begin():
            from src.app.migrations import Workload
            stmt = update(Workload).where(Workload.task_id == task_id).values(status="running")
            await session.execute(stmt)

    await asyncio.sleep(0.1) # Simulated model execution latency

    async with async_session() as session:
        async with session.begin():
            from src.app.migrations import Workload
            mock_result = {
                "detected_objects_count": 42,
                "metrics": {"latency_ms": 110},
                "artifact_url": uri
            }
            stmt = update(Workload).where(Workload.task_id == task_id).values(
                status="completed",
                result=mock_result
            )
            await session.execute(stmt)
    logger.info(f"Task {task_id} successfully marked as completed.")

async def main():
    logger.info("Starting background Inference Worker consumer...")
    connection = await aio_pika.connect_robust(RMQ_URL)
    
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue("ml_workloads_queue", durable=True)
        
        async with queue.iterator() as queue_iter:
            async for queue_message in queue_iter:
                async with queue_message.process():
                    try:
                        payload = json.loads(queue_message.body.decode())
                        await process_task(payload)
                    except Exception as err:
                        logger.error(f"Error processing background message context: {str(err)}")

if __name__ == "__main__":
    asyncio.run(main())
