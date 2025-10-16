#!/bin/bash

# TubeAgent Full Stack Startup Script
# This script starts both frontend and backend servers

echo "🚀 Starting TubeAgent (Frontend + Backend)..."
echo ""

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "🛑 Shutting down servers..."
    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null
    fi
    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null
    fi
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found!"
    echo "📝 Please copy .env.example to .env and add your API keys:"
    echo "   cp .env.example .env"
    echo "   Then edit .env to add your actual API keys"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "🐍 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment and install dependencies
echo "🔧 Setting up backend environment..."
source venv/bin/activate
pip install -q -r requirements.txt

# Start backend server in background
echo "🌐 Starting backend server on http://localhost:5050..."
PYTHONPATH=./src uvicorn app.main:app --reload --port 5050 --host 127.0.0.1 --ws-ping-interval 0 --ws-ping-timeout 60 > backend.log 2>&1 &
BACKEND_PID=$!

# Give backend a moment to start
sleep 2

# Check if backend started successfully
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Backend failed to start. Check backend.log for details."
    cat backend.log
    exit 1
fi

# Start frontend server in background
echo "🎨 Starting frontend server on http://localhost:5173..."
cd web
npm run dev > ../frontend.log 2>&1 &
FRONTEND_PID=$!
cd ..

# Give frontend a moment to start
sleep 3

# Check if frontend started successfully
if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo "❌ Frontend failed to start. Check frontend.log for details."
    cat frontend.log
    cleanup
    exit 1
fi

echo ""
echo "✅ Both servers are running!"
echo "🌐 Frontend: http://localhost:5173"
echo "🔧 Backend:  http://localhost:5050"
echo ""
echo "📝 Logs:"
echo "   Backend:  backend.log"
echo "   Frontend: frontend.log"
echo ""
echo "🔄 Auto-reload is enabled for both servers"
echo "🛑 To stop both servers, press Ctrl+C"
echo ""

# Wait for user interrupt
tail -f backend.log frontend.log &
TAIL_PID=$!

wait $BACKEND_PID $FRONTEND_PID
cleanup