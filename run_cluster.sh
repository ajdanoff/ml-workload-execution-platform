#!/bin/bash

# 1. Build project stage instances and spin up the complete network in the background
docker compose up --build -d

# 2. Check the real-time running health status of your infrastructure components
docker compose ps

# 3. Stream cluster logs dynamically to monitor Outbox-to-Worker event handshakes
docker compose logs -f api outbox_publisher inference_worker

# 4. Horizontal Scaling: Spin up two extra compute worker nodes to handle parallel ML queues
docker compose up -d --scale inference_worker=3

# 5. Clean teardown: Turn off the stack and wipe local container assets safely
docker compose down -v
