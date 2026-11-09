#!/bin/bash
set -e

echo "====================================================================="
echo "🐳 Cluster Initialization Protocol Started"
echo "====================================================================="

# 1. Parse database connection hostnames dynamically out of your Compose URL patterns
DB_HOST=$(echo $DATABASE_URL | sed -e 's/tcp:\/\///' -e 's/postgresql+asyncpg:\/\///' -e 's/.*@//' -e 's/:.*//')
DB_PORT=$(echo $DATABASE_URL | sed -e 's/.*://' -e 's/\/.*//')

CH_HOST=${CLICKHOUSE_HOST:-clickhouse}
CH_PORT=${CLICKHOUSE_PORT:-8123}

# Default fallbacks if parsing extraction loops hit complex string tokens
DB_HOST=${DB_HOST:-postgres}
DB_PORT=${DB_PORT:-5432}

echo "⏳ Awaiting PostgreSQL Availability ($DB_HOST:$DB_PORT)..."
until python -c "import socket; s = socket.socket(); s.settimeout(1); s.connect(('$DB_HOST', $DB_PORT))" 2>/dev/null; do
    sleep 0.5
done
echo "✅ PostgreSQL connection verified."

echo "⏳ Awaiting ClickHouse HTTP API Availability ($CH_HOST:$CH_PORT)..."
until python -c "import socket; s = socket.socket(); s.settimeout(1); s.connect(('$CH_HOST', $CH_PORT))" 2>/dev/null; do
    sleep 0.5
done
echo "✅ ClickHouse connection verified."

# 2. Programmatically initialize table schema architectures sequentially
if [ "$APP_ENV" = "test" ] || [ "$ENV" = "production" ]; then
    echo "🗄️ Executing structural table migrations..."
    
    # Synchronizes Postgres Workloads & Transactional Outbox tables safely
    python -m src.app.migrations
    
    # Dynamically injects ClickHouse analytical Log MergeTree schema blocks if present
    if python -c "import importlib.util; exit(0 if importlib.util.find_spec('src.app.clickhouse_init') else 1)" 2>/dev/null; then
        echo "📊 Initializing ClickHouse OLAP schema layers..."
        python -c "from src.app.clickhouse_init import init_clickhouse; init_clickhouse()"
    fi
fi

echo "====================================================================="
echo "🚀 Initialization Successful. Handoff to runtime command loop."
echo "====================================================================="

# Execute whatever runtime loop payload was passed down via Docker CMD directives (e.g. uvicorn, pytest, worker)
exec "$@"
