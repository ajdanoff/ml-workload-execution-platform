#!/bin/bash
set -e

# Automatically run the migrations block script 
echo "Executing database migrations..."
python -m src.app.migrations

# Execute whatever command was passed via CMD (e.g. uvicorn, pytest, worker loop)
exec "$@"
