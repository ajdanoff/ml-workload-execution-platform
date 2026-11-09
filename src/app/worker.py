import asyncio
import json
import logging
import os
import signal
import aio_pika
from sqlalchemy import update
from src.app.migrations import Workload, init_db_pool, async_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")
RMQ_URL = os.getenv("RABBITMQ_URL")

# In src/app/worker.py
from src.app.telemetry import log_inference_telemetry # Import our new analytical writer

async def process_task(payload: dict, message: aio_pika.IncomingMessage):
    task_id = payload.get("task_id") or payload.get("idempotency_key")
    uri = payload.get("input_uri", "")
    model = payload.get("model", "unknown")
    logger.info(f"Worker picked up Task ID: {task_id}. Evaluation parameters...")

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
        
        # 📊 EMIT FAIL TELEMETRY: Record failed processing metrics instantly to ClickHouse
        await log_inference_telemetry(task_id=task_id, model_name=model, latency_ms=0.0, objects_detected=0, status="failed")
        return

    # ➔ Scenario B: Native Cross-container Worker SIGKILL Crash Check
    if "suicidal-crash" in str(uri):
        if not message.redelivered:
            logger.warning(f"Chaos trigger detected: Executing instant OS SIGKILL for task {task_id}")
            # NOTE: We purposefully skip logging to ClickHouse here because a true SIGKILL 
            # should prevent any code execution, allowing our Watchdog alerts to catch the gap.
            os.kill(os.getpid(), signal.SIGKILL)
            return

    # Standard Successful Processing Path
    async with async_session() as session:
        async with session.begin():
            from src.app.migrations import Workload
            stmt = update(Workload).where(Workload.task_id == task_id).values(status="running")
            await session.execute(stmt)

    # Simulated Weight Ingestion/Inference latency parameters
    simulated_latency = 110.0
    simulated_objects_found = 42
    await asyncio.sleep(0.1) 

    async with async_session() as session:
        async with session.begin():
            from src.app.migrations import Workload
            mock_result = {
                "detected_objects_count": simulated_objects_found,
                "metrics": {"latency_ms": simulated_latency},
                "artifact_url": uri
            }
            stmt = update(Workload).where(Workload.task_id == task_id).values(
                status="completed",
                result=mock_result
            )
            await session.execute(stmt)
            
    # 📊 EMIT SUCCESS TELEMETRY: Ship analytics logs to ClickHouse outside the state database transaction
    await log_inference_telemetry(
        task_id=task_id,
        model_name=model,
        latency_ms=simulated_latency,
        objects_detected=simulated_objects_found,
        status="success"
    )
    logger.info(f"Task {task_id} successfully marked as completed.")

async def main():
    logger.info("Starting background Inference Worker consumer...")
    connection = await aio_pika.connect_robust(RMQ_URL)
    
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue_instance = await channel.declare_queue("ml_workloads_queue", durable=True)
        
        logger.info("Worker listening to 'ml_workloads_queue'. Awaiting events...")
        async with queue_instance.iterator() as queue_iter:
            async for queue_message in queue_iter:
                # Manual AMQP ACK handling context block wrapper
                async with queue_message.process():
                    try:
                        payload = json.loads(queue_message.body.decode())
                        await process_task(payload, queue_message)
                    except Exception as err:
                        logger.error(f"Error processing background message context: {str(err)}")

if __name__ == "__main__":
    asyncio.run(main())
