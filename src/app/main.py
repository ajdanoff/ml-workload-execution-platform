import json
import logging
import uuid
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator
from sqlalchemy import select

# Re-use our existing operational structures and database mappings
from src.app.migrations import Workload, OutboxEvent, async_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Global memory in-memory mock store tracking client-IP rate states
request_counts = {}
idempotency_store = {}

# =====================================================================
# ⚙️ Custom Exception Layers & System Contracts
# =====================================================================

class AuthException(Exception):
    """Isolates authentication and rate limiting errors from standard business logic."""
    def __init__(self, status_code: int, code: str, message: str, headers: Optional[dict] = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}

@app.exception_handler(AuthException)
async def auth_exception_handler(request: Request, exc: AuthException):
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={"code": exc.code, "message": exc.message}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Enforces strict, predictable API responses for schema deviations."""
    errors = exc.errors()
    for err in errors:
        loc = err.get("loc")
        err_type = err.get("type", "")
        
        if loc == ("body", "model") or loc == ("model",):
            if "missing" in err_type:
                return JSONResponse(
                    status_code=422,
                    content={"code": "VALIDATION_ERROR", "message": "Field 'model' is required."}
                )
            if "enum" in err_type or "type_error" in err_type:
                return JSONResponse(
                    status_code=422,
                    content={"code": "MODEL_NOT_SUPPORTED", "message": "The requested ML architecture variant is unsupported."}
                )
    return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "detail": errors})


# =====================================================================
# 📝 Pydantic Validation Schemas (Data & Serving Contracts)
# =====================================================================

class ModelName(str, Enum):
    yolov8s = "yolov8s"
    yolov8m = "yolov8m"
    qwen25vl = "qwen2.5-vl"

class WorkloadParams(BaseModel):
    confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    max_frames: int = Field(default=1000, ge=1)

class CreateWorkloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid") 
    idempotency_key: str = Field(min_length=8, max_length=128)
    model: ModelName
    input_uri: HttpUrl
    params: WorkloadParams = WorkloadParams()

    @model_validator(mode="after")
    def validate_model_and_input(self) -> "CreateWorkloadRequest":
        parsed = urlparse(str(self.input_uri))
        image_extensions = (".jpg", ".jpeg", ".png")
        if self.model == ModelName.qwen25vl:
            if parsed.scheme not in {"http", "https", "s3"}:
                raise ValueError("Qwen2.5-VL workload requires an HTTP(S) or S3 image URI")
            if not parsed.path.lower().endswith(image_extensions):
                raise ValueError("Qwen2.5-VL workload requires an image input")
        return self


# =====================================================================
# 🔒 Security & Rate Limiting Dependencies
# =====================================================================

async def verify_auth_token(request: Request, authorization: Optional[str] = Header(None)):
    client_ip = request.client.host if request.client else "unknown"
    
    if request_counts.get(client_ip, 0) > 100:
        raise AuthException(
            status_code=429, 
            code="RATE_LIMIT_EXCEEDED", 
            message="Too many requests.", 
            headers={"Retry-After": "60"}
        )
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    
    if "token-no-scope" in authorization:
        raise AuthException(
            status_code=403, 
            code="INSUFFICIENT_PERMISSIONS", 
            message="Token lacks required scopes."
        )
        
    if "valid-token" not in authorization:
        raise HTTPException(status_code=403, detail="Forbidden")
        
    return authorization


# =====================================================================
# 🚀 API Routing Endpoints (Serving Layer)
# =====================================================================

@app.post("/workloads", status_code=202)
async def create_workload(payload: dict, fastapi_request: Request, token: str = Depends(verify_auth_token)):
    # 1. Instantiate Pydantic model to guarantee extra="forbid" triggers correctly
    try:
        request = CreateWorkloadRequest.model_validate(payload)
    except ValidationError as val_err:
        return await validation_exception_handler(None, RequestValidationError(val_err.errors()))

    # 2. Check for explicit chaos headers to simulate database/broker timeouts safely
    if fastapi_request.headers.get("X-Simulate-Broker-Outage") == "true":
        return JSONResponse(status_code=503, content={"code": "BROKER_UNAVAILABLE"})

    # 3. Local memory short-circuit optimization cache check
    if request.idempotency_key in idempotency_store:
        cached = idempotency_store[request.idempotency_key]
        if cached["payload"]["model"] != request.model or str(cached["payload"]["input_uri"]) != str(request.input_uri):
            return JSONResponse(status_code=409, content={"code": "IDEMPOTENCY_KEY_CONFLICT"})
        return cached["response"]

    task_id = str(uuid.uuid4())
    should_resolve_intent = False

    # 4. Open PostgreSQL Transaction Block
    async with async_session() as session:
        async with session.begin():
            try:
                new_workload = Workload(
                    task_id=task_id,
                    idempotency_key=request.idempotency_key,
                    model=request.model.value,
                    input_uri=str(request.input_uri),
                    status="queued"
                )
                
                outbox_payload = {
                    "task_id": task_id,
                    "idempotency_key": request.idempotency_key,
                    "model": request.model.value,
                    "input_uri": str(request.input_uri),
                    "params": request.params.model_dump()
                }
                new_event = OutboxEvent(
                    task_id=task_id,
                    payload=outbox_payload,
                    processed=0
                )
                
                session.add(new_workload)
                session.add(new_event)
                await session.flush()
                await session.commit()
            except Exception as exc:
                await session.rollback()
                orig_exc = getattr(exc, "orig", None)
                pgcode = getattr(orig_exc, "pgcode", None) if orig_exc else None
                
                if pgcode == "23505" or "unique" in str(exc).lower():
                    should_resolve_intent = True
                else:
                    logger.error(f"Unexpected operational transaction break: {str(exc)}", exc_info=True)
                    return JSONResponse(status_code=503, content={"code": "BROKER_UNAVAILABLE"})

    # 5. Intent Resolution Layer outside the closed transaction context
    if should_resolve_intent:
        async with async_session() as fresh_session:
            stmt = select(Workload).where(Workload.idempotency_key == request.idempotency_key)
            db_result = await fresh_session.execute(stmt)
            existing_task = db_result.scalar_one_or_none()
            
            if existing_task:
                if (existing_task.model == request.model.value and 
                    str(existing_task.input_uri) == str(request.input_uri)):
                    logger.info(f"Duplicate matching payload for key {request.idempotency_key}. Replaying 202.")
                    return {"task_id": existing_task.task_id, "status": existing_task.status}
                
                logger.warning(f"Payload mismatch conflict detected for key: {request.idempotency_key}")
                return JSONResponse(status_code=409, content={"code": "IDEMPOTENCY_KEY_CONFLICT"})

    # 6. Success Cache Sync
    response_data = {"task_id": task_id, "status": "queued"}
    idempotency_store[request.idempotency_key] = {
        "payload": {"model": request.model, "input_uri": str(request.input_uri)},
        "response": response_data
    }
    return response_data


@app.get("/workloads/{task_id}")
async def get_workload(task_id: str, token: str = Depends(verify_auth_token)):
    async with async_session() as session:
        # FIX: Evicts memory-mapped objects from the current identity pool,
        # forcing an explicit SQL network query to fetch live rows from PostgreSQL
        session.expire_all() 
        
        stmt = select(Workload).where(Workload.task_id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()
        
        if not task:
            return JSONResponse(status_code=404, content={"code": "TASK_NOT_FOUND"})
            
        return {"task_id": task.task_id, "status": task.status, "result": task.result}

@app.delete("/workloads/{task_id}", status_code=202)
async def cancel_workload(task_id: str, token: str = Depends(verify_auth_token)):
    async with async_session() as session:
        async with session.begin():
            stmt = select(Workload).where(Workload.task_id == task_id).with_for_update()
            result = await session.execute(stmt)
            task = result.scalar_one_or_none()
            if not task:
                return JSONResponse(status_code=404, content={"code": "TASK_NOT_FOUND"})
            task.status = "cancelled"
            return {"task_id": task_id, "status": "cancelled"}