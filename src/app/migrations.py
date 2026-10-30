import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, JSON, Integer, DateTime, func

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fetch database string securely from container env context
import os
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://ml_admin:ml_secure_password@postgres:5432/ml_workloads_db")

Base = declarative_base()

class Workload(Base):
    __tablename__ = "workloads"
    
    task_id = Column(String(36), primary_key=True, index=True)
    # The source of truth for our strict single-task execution policy
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    model = Column(String(32), nullable=False)
    input_uri = Column(String(512), nullable=False)
    status = Column(String(32), default="queued", nullable=False)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(36), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    processed = Column(Integer, default=0, nullable=False, index=True) # 0 = Pending, 1 = Shipped
    created_at = Column(DateTime, default=func.now(), nullable=False)

async def run_migrations():
    logger.info("Initializing connection to database engine...")
    engine = create_async_engine(DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        logger.info("Creating relational table architectures...")
        # FIX: Changed from create_base_all to create_all
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("Database schemas fully synced. Step complete.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_migrations())
