#!/bin/bash
cd "$(dirname "$0")"

python3 -m pip install -r requirements.txt -q 2>/dev/null || python -m pip install -r requirements.txt -q

python3 -m uvicorn api:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

cd frontend && npm run start &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
