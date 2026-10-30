#!/bin/bash

# 1. Apply the complete platform configuration resources to the active cluster context
kubectl apply -f k8s-deployment.yaml

# 2. Monitor the orchestration states of your newly allocated Pod instances
kubectl get pods -w

# 3. Stream real-time logs from the isolated Outbox Publisher component
kubectl logs -f deployment/ml-platform-outbox-publisher

# 4. Horizontally scale inference execution workers to match heavy pipeline processing queues
kubectl scale deployment/ml-platform-worker --replicas=5
