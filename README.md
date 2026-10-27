# ML Workload Execution Platform

An asynchronous FastAPI engine designed to handle heavy ML/Computer Vision inference pipelines (e.g., YOLOv8) without blocking incoming API routing loops.

## 🚀 Getting Started

### 1. Initialize Virtual Environment & Dependencies
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install fastapi[standard] pytest pytest-asyncio httpx pytest-mock
```

### 2. Launch Local Server
```bash
fastapi dev src/app/main.py
```

### 3. Run Asynchronous Test Suite
```bash
pytest
```
