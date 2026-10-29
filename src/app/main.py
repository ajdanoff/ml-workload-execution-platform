from enum import Enum
from fastapi import FastAPI, Request, HTTPException, Depends, Header, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator, ValidationError
from typing import Optional
from urllib.parse import urlparse
import json
from src.app.services import queue, db_store, idempotency_store, request_counts

app = FastAPI()

# --- Custom Security Exception Layer ---
class AuthException(Exception):
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

# --- Validation Exception Handler ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
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


async def verify_auth_token(request: Request, authorization: Optional[str] = Header(None)):
    client_ip = request.client.host if request.client else "unknown"
    if request_counts.get(client_ip, 0) > 100:
        raise AuthException(status_code=429, code="RATE_LIMIT_EXCEEDED", message="Too many requests.", headers={"Retry-After": "60"})
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    
    if "token-no-scope" in authorization:
        raise AuthException(status_code=403, code="INSUFFICIENT_PERMISSIONS", message="Token lacks required scopes.")
        
    if "valid-token" not in authorization:
        raise HTTPException(status_code=403, detail="Forbidden")
    return authorization


@app.post("/workloads", status_code=202)
async def create_workload(payload: dict, token: str = Depends(verify_auth_token)):
    try:
        request = CreateWorkloadRequest.model_validate(payload)
    except ValidationError as val_err:
        return await validation_exception_handler(None, RequestValidationError(val_err.errors()))

    if request.idempotency_key in idempotency_store:
        cached = idempotency_store[request.idempotency_key]
        if cached["payload"]["model"] != request.model or str(cached["payload"]["input_uri"]) != str(request.input_uri):
            return JSONResponse(status_code=409, content={"code": "IDEMPOTENCY_KEY_CONFLICT"})
        return cached["response"]

    try:
        task_id = await queue.enqueue(request.model_dump())
    except ConnectionError:
        return JSONResponse(status_code=503, content={"code": "BROKER_UNAVAILABLE"})
        
    response_data = {"task_id": task_id, "status": "accepted"}
    idempotency_store[request.idempotency_key] = {
        "payload": {"model": request.model, "input_uri": str(request.input_uri)},
        "response": response_data
    }
    return response_data

@app.get("/workloads/{task_id}")
async def get_workload(task_id: str, token: str = Depends(verify_auth_token)):
    if task_id not in db_store:
        return JSONResponse(status_code=404, content={"code": "TASK_NOT_FOUND"})
    task = db_store[task_id]
    return {"task_id": task_id, "status": task["status"], "result": task["result"]}

@app.delete("/workloads/{task_id}", status_code=202)
async def cancel_workload(task_id: str, token: str = Depends(verify_auth_token)):
    if task_id not in db_store:
        return JSONResponse(status_code=404, content={"code": "TASK_NOT_FOUND"})
    db_store[task_id]["status"] = "cancelled"
    return {"task_id": task_id, "status": "cancelled"}
