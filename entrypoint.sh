#!/bin/bash
set -e

echo "Executing database migrations..."
python -m src.app.migrations

# Fix: Intercept binary calls and route them safely through the python module runtime wrapper
if [ "$1" = "uvicorn" ]; then
    echo "Starting FastAPI Server via Python Module context..."
    shift
    exec python -m uvicorn "$@"
elif [ "$1" = "pytest" ]; then
    echo "Starting Pytest Suite via Python Module context..."
    shift
    exec python -m pytest "$@"
else
    # Keep standard execution for direct worker loops
    exec "$@"
fi
