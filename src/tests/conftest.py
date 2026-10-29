import pytest
from httpx import AsyncClient, ASGITransport
from src.app.main import app, request_counts
from src.app.services import db_store, idempotency_store, reset_process_pool, queue, heavy_inference_worker

@pytest.fixture(autouse=True)
def clean_stores_and_pool():
    db_store.clear()
    idempotency_store.clear()
    request_counts.clear()
    queue.worker_function = heavy_inference_worker
    queue.is_connected = True
    reset_process_pool()    

@pytest.fixture
async def client():
    """Provides a reusable async HTTP client pre-configured with active auth headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as ac:
        yield ac
