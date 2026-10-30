import asyncio
import logging
import json
import os
import aio_pika
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, update
from src.app.migrations import OutboxEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")
RMQ_URL = os.getenv("RABBITMQ_URL")

async def main():
    logger.info("Outbox Relay initialization starting...")
    engine = create_async_engine(DB_URL)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    # Establish persistent AMQP connectivity to the message broker node
    connection = await aio_pika.connect_robust(RMQ_URL)
    
    async with connection:
        channel = await connection.channel()
        
        # Declare a durable tasks queue matching quorum guidelines
        queue = await channel.declare_queue("ml_workloads_queue", durable=True)
        logger.info("Connected to RabbitMQ channel. Monitoring database intents...")
        
        while True:
            async with async_session() as session:
                async with session.begin():
                    # Pull next batch of unhandled outbox processing markers
                    stmt = select(OutboxEvent).where(OutboxEvent.processed == 0).limit(10).with_for_update()
                    result = await session.execute(stmt)
                    events = result.scalars().all()
                    
                    for event in events:
                        try:
                            # Deliver the message cleanly to the task queue
                            await channel.default_exchange.publish(
                                aio_pika.Message(
                                    body=json.dumps(event.payload).encode(),
                                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                                ),
                                routing_key="ml_workloads_queue"
                            )
                            
                            # Mark event as successfully processed inside the database transaction boundary
                            event.processed = 1
                            logger.info(f"Successfully relayed task {event.task_id} event to RabbitMQ.")
                        except Exception as e:
                            logger.error(f"Failed to publish event {event.id}: {str(e)}")
            
            # Non-blocking sweep poll latency interval
            await asyncio.sleep(1.0)

if __name__ == "__main__":
    asyncio.run(main())
