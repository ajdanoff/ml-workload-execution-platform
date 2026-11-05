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

async def process_task(payload: dict, message: aio_pika.IncomingMessage):
    task_id = payload.get("task_id") or payload.get("idempotency_key")
    uri = payload.get("input_uri", "")

    logger.info(
        "Worker picked up task",
        extra={
            "task_id": task_id,
            "redelivered": message.redelivered,
            "delivery_tag": message.delivery_tag,
        },
    )

    init_db_pool()

    if "missing-file" in uri:
        logger.warning("Chaos trigger: missing S3 object", extra={"task_id": task_id})

        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(Workload)
                    .where(Workload.task_id == task_id)
                    .values(
                        status="failed",
                        result={
                            "error": "INPUT_NOT_FOUND: S3 Object not found in bucket"
                        },
                    )
                )
        return

    if "suicidal-crash" in uri and not message.redelivered:
        logger.warning(
            "Chaos trigger: simulating SIGKILL before ACK",
            extra={"task_id": task_id},
        )

        # Do not ack. Do not update DB to completed.
        # Kill the whole worker process.
        os.kill(os.getpid(), signal.SIGKILL)

    if "suicidal-crash" in uri:
        logger.info(
            "Redelivered task: continuing normal processing",
            extra={"task_id": task_id},
        )

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(Workload)
                .where(Workload.task_id == task_id)
                .values(status="running")
            )

    await asyncio.sleep(0.1)

    mock_result = {
        "detected_objects_count": 42,
        "metrics": {"latency_ms": 110},
        "artifact_url": uri,
    }

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(Workload)
                .where(Workload.task_id == task_id)
                .values(
                    status="completed",
                    result=mock_result,
                )
            )

    logger.info("Task completed", extra={"task_id": task_id})

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
