import asyncio
import uuid
import os
import signal
from concurrent.futures import ProcessPoolExecutor

db_store = {}
idempotency_store = {}
pool = ProcessPoolExecutor(max_workers=2)
request_counts = {}

def reset_process_pool():
    global pool
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    pool = ProcessPoolExecutor(max_workers=2)

def heavy_inference_worker(payload: dict) -> dict:
    return {"detected_objects_count": 42, "metrics": {"latency_ms": 150}}

def broken_s3_inference_worker(payload: dict) -> dict:
    raise FileNotFoundError("INPUT_NOT_FOUND")

def suicidal_inference_worker(payload: dict) -> dict:
    os.kill(os.getpid(), signal.SIGKILL)
    return {"status": "won't reach here"}

class AsyncQueue:
    def __init__(self):
        self.is_connected = True
        self.worker_function = heavy_inference_worker

    async def enqueue(self, payload: dict) -> str:
        if not self.is_connected:
            raise ConnectionError("Broker connection timed out")
            
        task_id = str(uuid.uuid4())
        db_store[task_id] = {
            "status": "accepted",
            "payload": payload,
            "result": None
        }
        
        asyncio.create_task(self._run_background_inference(task_id, payload))
        return task_id

    async def _run_background_inference(self, task_id: str, payload: dict):
        try:
            if db_store.get(task_id, {}).get("status") == "cancelled":
                return
            db_store[task_id]["status"] = "running"
            
            global pool
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(pool, self.worker_function, payload)
            
            if db_store.get(task_id, {}).get("status") == "cancelled":
                return
                
            db_store[task_id]["status"] = "completed"
            db_store[task_id]["result"] = result
        except Exception as e:
            if db_store.get(task_id, {}).get("status") != "cancelled":
                db_store[task_id]["status"] = "failed"
                db_store[task_id]["result"] = {"error": str(e)}

queue = AsyncQueue()
