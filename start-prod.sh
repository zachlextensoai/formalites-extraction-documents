#!/bin/bash
cd "$(dirname "$0")"

FRONTEND_PID=""
BACKEND_PID=""

cleanup() {
  [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
  [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null
}
trap cleanup EXIT

echo "Installing Python dependencies..."
pip install -r requirements.txt -q 2>&1 || echo "Warning: pip install had issues, continuing..."

echo "Starting backend on port 8000..."
python3 -m uvicorn api:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "Waiting for backend to be ready..."
for i in $(seq 1 30); do
  if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "Backend process died unexpectedly."
    break
  fi
  if curl -s http://localhost:8000/api/config > /dev/null 2>&1; then
    echo "Backend is ready."
    break
  fi
  sleep 1
done

echo "Starting frontend on port 5000..."
cd frontend && npm run start &
FRONTEND_PID=$!

wait
