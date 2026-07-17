#!/bin/bash
set -e

IMAGE_NAME=agent-contracts-runner

# Deploy the long-running stack (redis, jaeger, supplier, client) via Compose.
docker compose up -d --build redis jaeger supplier client

SUPPLIER_CID=$(docker compose ps -q supplier)
NETWORK=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$SUPPLIER_CID")

docker build --tag "$IMAGE_NAME" --file Dockerfile.runner .
docker run --rm \
  --network "$NETWORK" \
  -e SUPPLIER_URL=http://localhost:8000/ \
  -e CLIENT_SERVICE_URL=http://client:8000/ \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/messages:/app/messages" \
  "$IMAGE_NAME"

docker compose down