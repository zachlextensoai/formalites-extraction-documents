#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

FRONTEND_PID=""
BACKEND_PID=""

cleanup() {
  [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
  [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null
}
trap cleanup EXIT

.venv/bin/python -m pip install -r requirements.txt -q 2>/dev/null

.venv/bin/python -m uvicorn api:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "Waiting for backend to be ready on port 8000..."
for i in $(seq 1 30); do
  if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "Backend process died. Exiting."
    exit 1
  fi
  if curl -s http://localhost:8000/api/config > /dev/null 2>&1; then
    echo "Backend is ready."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Backend failed to become ready after 30s. Exiting."
    exit 1
  fi
  sleep 1
done

cd frontend && npm run start &
FRONTEND_PID=$!

wait
