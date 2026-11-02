import pytest
from httpx import AsyncClient, ASGITransport
from src.app.main import app, idempotency_store, request_counts
from src.app.migrations import init_db_pool, engine, Workload, OutboxEvent
from src.app.services import queue, heavy_inference_worker

@pytest.fixture(autouse=True)
async def clean_database_and_services():
    """Guarantees complete database isolation and session closure between tests."""
    # 1. Force the engine connection pools to refresh and re-bind to the current loop context
    init_db_pool()
    
    # 2. Clean out tables using a raw, stateless execution mapping to prevent loop conflicts
    global engine
    async with engine.begin() as conn:
        await conn.execute(Workload.__table__.delete())
        await conn.execute(OutboxEvent.__table__.delete())
    
    # 3. Reset system memory cache trackers and service hooks
    request_counts.clear()
    queue.worker_function = heavy_inference_worker
    queue.is_connected = True
    
    yield
    
    # 4. Dispose engine immediately after test completion to release connections cleanly
    await engine.dispose()

@pytest.fixture
async def client():
    """Provides a clean async client instance matching the network topology mapping."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as ac:
        yield ac

@pytest.fixture(autouse=True)
async def clean_database_and_services():
    init_db_pool()
    global engine
    async with engine.begin() as conn:
        await conn.execute(Workload.__table__.delete())
        await conn.execute(OutboxEvent.__table__.delete())
    
    # Clean memory cache stores to avoid leak pollution across tests
    request_counts.clear()
    idempotency_store.clear() # Added to isolate idempotency test histories
    queue.worker_function = heavy_inference_worker
    queue.is_connected = True
    
    yield
    await engine.dispose()
