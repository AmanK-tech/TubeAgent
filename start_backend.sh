#!/bin/bash

# TubeAgent Backend Startup Script
# This script starts the backend server with proper environment loading

echo "🚀 Starting TubeAgent Backend..."
echo "📁 Loading environment from .env file..."

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

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# Start the backend server
echo "🌐 Starting API server on http://localhost:5050"
echo "🔄 Auto-reload enabled - the server will restart when you make changes"
echo ""
echo "To stop the server, press Ctrl+C"
echo ""

# Set PYTHONPATH and start uvicorn
PYTHONPATH=./src uvicorn app.main:app --reload --port 5050 --host 127.0.0.1 --ws-ping-interval 0 --ws-ping-timeout 60