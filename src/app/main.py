from fastapi import FastAPI, Request, HTTPException, Depends, Header
from pydantic import BaseModel, Field
from typing import Optional, Literal
from src.app.services import queue, db_store, idempotency_store

app = FastAPI()

# Pydantic schemas for built-in baseline documentation and edge validation
class MLParams(BaseModel):
    confidence: float = Field(..., ge=0.0, le=1.0)
    max_frames: int = Field(..., gt=0)

class WorkloadRequest(BaseModel):
    idempotency_key: str
    model: Literal["yolov8s", "yolov8m", "yolov8x"]
    input_uri: str = Field(..., pattern=r"^s3://.+")
    params: Optional[MLParams] = None

# Simple security stub function
async def verify_auth_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    if "valid-token" not in authorization:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid scopes")
    return authorization

from asyncio import Lock

idempotency_lock = Lock()

@app.post("/workloads", status_code=202)
async def create_workload(request: WorkloadRequest, token: str = Depends(verify_auth_token)):
    # Handle Idempotency 
    async with idempotency_lock:
        if request.idempotency_key in idempotency_store:
            return idempotency_store[request.idempotency_key]
    
        # Process Payload Allocation
        try:
            task_id = await queue.enqueue(request.model_dump())
        except ConnectionError as e:
            raise HTTPException(status_code=503, detail="Service Unavailable: Queue Broker Offline")
        
        response_data = {"task_id": task_id, "status": "accepted"}
        idempotency_store[request.idempotency_key] = response_data
        return response_data

@app.get("/workloads/{task_id}")
async def get_workload(task_id: str, token: str = Depends(verify_auth_token)):
    if task_id not in db_store:
        raise HTTPException(status_code=404, detail=f"Workload {task_id} not found")
    task = db_store[task_id]
    return {"task_id": task_id, "status": task["status"], "result": task["result"]}
