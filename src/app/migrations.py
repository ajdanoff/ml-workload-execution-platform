import asyncio
import logging
import os
import sqlalchemy as sa  # <--- FIX: Added explicit SQLAlchemy namespace binding import
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, JSON, Integer, DateTime, func
from sqlalchemy.pool import NullPool # Import NullPool for stateless connections

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://ml_admin:ml_secure_password@postgres:5432/ml_workloads_db")
APP_ENV = os.getenv("APP_ENV", "production")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

def init_db_pool(url: str = DATABASE_URL):
    """Dynamically binds connection pools, disabling caching if running in a test context."""
    global engine, async_session
    
    # In testing environments, use NullPool to prevent open sockets from traversing loop lifecycles
    poolclass = NullPool if APP_ENV == "test" else None
    
    engine = create_async_engine(url, echo=False, poolclass=poolclass, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

# Initialize baseline mapping
init_db_pool()

Base = declarative_base()

class Workload(Base):
    __tablename__ = "workloads"
    task_id = Column(String(36), primary_key=True, index=True)
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
    processed = Column(Integer, default=0, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

# src/app/migrations.py
async def run_migrations():
    logger.info("Initializing connection to database engine...")
    global engine
    
    # Unique application identifier key for our distributed lock
    MIGRATION_LOCK_ID = 88888888
    
    # engine.begin() opens a single connection and starts a transaction atomically
    async with engine.begin() as conn:
        logger.info("Acquiring transactional advisory lock for schema migration...")
        # 1. Acquire the lock inside the active connection context
        await conn.execute(sa.text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_ID})"))
        
        try:
            logger.info("Creating relational table architectures...")
            # 2. Run the table synchronization safely within the lock window
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database schemas fully synced. Step complete.")
        finally:
            # 3. Always release the lock to free trailing container services
            logger.info("Releasing migration advisory lock.")
            await conn.execute(sa.text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_ID})"))
            
if __name__ == "__main__":
    asyncio.run(run_migrations())
