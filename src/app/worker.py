import asyncio
import json
import logging
import os
import aio_pika
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")
RMQ_URL = os.getenv("RABBITMQ_URL")

async def process_task(payload: dict, session_factory):
    task_id = payload.get("idempotency_key") # Or your task generation identifier
    logger.info(f"Beginning ML processing execution loop for Task ID: {task_id}")
    
    # 1. Update status to 'running' in PostgreSQL State Store
    async with session_factory() as session:
        async with session.begin():
            # Dynamically update the status of the record matching this task payload
            from src.app.migrations import Workload
            stmt = update(Workload).where(Workload.idempotency_key == task_id).values(status="running")
            await session.execute(stmt)
    
    # 2. Simulate fetching input artifact from S3/MinIO and running model inference
    await asyncio.sleep(0.1) # Simulated latency hook
    
    # 3. Save mock artifacts metrics and mark as 'completed'
    async with session_factory() as session:
        async with session.begin():
            mock_result = {
                "detected_objects_count": 42,
                "metrics": {"latency_ms": 110},
                "artifact_url": payload.get("input_uri")
            }
            stmt = update(Workload).where(Workload.idempotency_key == task_id).values(
                status="completed",
                result=mock_result
            )
            await session.execute(stmt)
    logger.info(f"Task {task_id} successfully marked as completed.")

async def main():
    logger.info("Starting background Inference Worker consumer...")
    engine = create_async_engine(DB_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    # Connect to robust RabbitMQ channel AMQP protocol
    connection = await aio_pika.connect_robust(RMQ_URL)
    
    async with connection:
        channel = await connection.channel()
        # Enforce consumer prefetch limits to distribute tasks evenly across worker nodes
        await channel.set_qos(prefetch_count=1)
        
        queue = await channel.declare_queue("ml_workloads_queue", durable=True)
        
        logger.info("Worker successfully listening to 'ml_workloads_queue'. Awaiting messages...")
        
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process(): # Manual AMQP ACK handling wrapper
                    try:
                        payload = json.loads(message.body.decode())
                        await process_task(payload, session_factory)
                    except Exception as err:
                        logger.error(f"Error processing background message context: {str(err)}")

if __name__ == "__main__":
    asyncio.run(main())
