import asyncio
import os
import signal
import uuid
from concurrent.futures import ProcessPoolExecutor

# In-memory stores acting as database and idempotency tables
db_store = {}
idempotency_store = {}
pool = ProcessPoolExecutor(max_workers=2)

def reset_process_pool():
    """Forces shutdown of the current pool and recreates it fresh"""
    global pool
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    pool = ProcessPoolExecutor(max_workers=2)

def heavy_inference_worker(payload: dict) -> dict:
    """Simulates CPU-bound ML model inference (e.g. YOLOv8)."""
    import time
    time.sleep(0.1)  # Simulated processing latency
    return {"detected_objects_count": 42, "model_used": payload["model"]}

# A pickable worker function designed to kill its own process mid-execution
def suicidal_inference_worker(payload: dict) -> dict:
    # Simulate abrupt uncatchable system crash (like SIGKIL / OOM killer)
    os.kill(os.getpid(), signal.SIGKILL)
    return {"status": "won't reach here"}

def broken_s3_inference_worker(payload: dict) -> dict:
    raise FileNotFoundError("S3 Object not found")


class AsyncQueue:
    
    worker_function = heavy_inference_worker
    
    def __init__(self):
        self.is_connected = True
        # Track which worker to trigger (can be altered dynamically in tests)
        # self.worker_function = heavy_inference_worker
    
    async def enqueue(self, payload: dict) -> str:
        if not self.is_connected:
            raise ConnectionError("Broker connection timed out (Simulated Outage)")
        
        await asyncio.sleep(0.01)
        task_id = str(uuid.uuid4())
        
        # Initialize internal task DB state
        db_store[task_id] = {
            "status": "accepted",
            "payload": payload,
            "result": None
        }
        
        # Fire off background worker
        asyncio.create_task(self._run_background_inference(task_id, payload))
        return task_id

    async def _run_background_inference(self, task_id: str, payload: dict):
        try:
            db_store[task_id]["status"] = "running"
            loop = asyncio.get_running_loop()
            
            result = await loop.run_in_executor(pool, self.worker_function, payload)
            
            db_store[task_id]["status"] = "completed"
            db_store[task_id]["result"] = result
        except Exception as e:
            db_store[task_id]["status"] = "failed"
            # Extract class name to capture structural process pool break exceptions
            db_store[task_id]["result"] = {"error": f"{type(e).__name__}: {str(e)}"}

queue = AsyncQueue()
