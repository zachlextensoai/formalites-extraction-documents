#!/bin/bash
cd "$(dirname "$0")"

pip install -r requirements.txt -q

python -m uvicorn api:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

cd frontend && npm run start &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
