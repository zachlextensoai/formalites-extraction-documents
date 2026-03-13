#!/bin/bash
# Start both FastAPI backend and Next.js frontend
cd "$(dirname "$0")"

# Start FastAPI backend on port 8000
.venv/bin/python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Start Next.js frontend on port 3000
cd frontend && npm run dev &
FRONTEND_PID=$!

# Wait for either to exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
