import pytest
from httpx import AsyncClient, ASGITransport
from src.app.main import app
from src.app.services import db_store, idempotency_store, reset_process_pool, queue, heavy_inference_worker

@pytest.fixture(autouse=True)
def clean_stores():
    """Clear memory DB states and reset the excecution environment between test runs."""
    db_store.clear()
    idempotency_store.clear()
    
    # 1. Reset worker process allocations back to normal baseline configurations
    queue.worker_function = heavy_inference_worker
    queue.is_connected = True
    
    # 2. Re-instantiate the ProcessPoolExecutor to heal any crashes from case 15
    reset_process_pool()
    

@pytest.fixture
async def client():
    """Provides a reusable async HTTP client pre-configured with active auth headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as ac:
        yield ac
