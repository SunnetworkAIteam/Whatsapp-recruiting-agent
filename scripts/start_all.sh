#!/usr/bin/env bash
#
# scripts/start_all.sh
#
# Starts every piece of the WhatsApp recruiting agent stack in the
# background, so a full day's work can begin with one command instead
# of manually opening 6+ terminals every time.
#
# Starts, in order:
#   1. Redis (Docker container, if not already running)
#   2. Main FastAPI app (port 8000)      -- /sync-sheets, /trigger-sync
#   3. Webhook FastAPI app (port 8001)   -- inbound WhatsApp replies
#   4. Celery worker                     -- sends messages, runs tasks
#   5. Celery beat                       -- periodic enqueue + follow-up cycle
#   6. ngrok (tunnels port 8001)
#   7. Dashboard (Vite dev server)
#
# Logs go to logs/<service>.log. PIDs are written to .pids so
# stop_all.sh can cleanly stop everything later.
#
# Usage:
#   bash scripts/start_all.sh
#
# After running, this script prints the ngrok URL -- if it's different
# from last time, update Twilio's webhook URL (Numbers & senders ->
# your WhatsApp sender -> Edit Sender -> Webhook URL for incoming
# messages) to <ngrok_url>/webhooks/whatsapp/inbound.

set -e

# Resolve the repo root regardless of where this script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

mkdir -p logs
PID_FILE="$REPO_ROOT/.pids"
> "$PID_FILE"  # truncate/create fresh each run

echo "Repo root: $REPO_ROOT"

# ---------------------------------------------------------------------------
# 1. Redis
# ---------------------------------------------------------------------------
echo "Checking Redis..."
REDIS_CONTAINER="whatsapp-recruiting-agent-redis-1"
if [ "$(docker ps -q -f name=$REDIS_CONTAINER)" ]; then
  echo "  Redis already running."
elif [ "$(docker ps -aq -f name=$REDIS_CONTAINER)" ]; then
  echo "  Starting existing Redis container..."
  docker start $REDIS_CONTAINER > /dev/null
else
  echo "  No existing Redis container found. Creating one..."
  docker run -d --name $REDIS_CONTAINER -p 6379:6379 redis:7 > /dev/null
fi
sleep 1

# ---------------------------------------------------------------------------
# Activate venv for all Python processes below
# ---------------------------------------------------------------------------
source venv/bin/activate

# ---------------------------------------------------------------------------
# 2. Main FastAPI app (port 8000)
# ---------------------------------------------------------------------------
echo "Starting main app (port 8000)..."
nohup python -m uvicorn app.main:app --reload --port 8000 > logs/main.log 2>&1 &
echo "main:$!" >> "$PID_FILE"

# ---------------------------------------------------------------------------
# 3. Webhook FastAPI app (port 8001)
# ---------------------------------------------------------------------------
echo "Starting webhook app (port 8001)..."
TWILIO_VALIDATE_SIGNATURE=false nohup python -m uvicorn webhook.app:app --reload --port 8001 > logs/webhook.log 2>&1 &
echo "webhook:$!" >> "$PID_FILE"

# ---------------------------------------------------------------------------
# 4. Celery worker
# ---------------------------------------------------------------------------
echo "Starting Celery worker..."
nohup celery -A app.celery_app worker --loglevel=info > logs/worker.log 2>&1 &
echo "worker:$!" >> "$PID_FILE"

# ---------------------------------------------------------------------------
# 5. Celery beat
# ---------------------------------------------------------------------------
echo "Starting Celery beat..."
nohup celery -A app.celery_app beat --loglevel=info > logs/beat.log 2>&1 &
echo "beat:$!" >> "$PID_FILE"

# ---------------------------------------------------------------------------
# 6. ngrok (tunnels the webhook app on port 8001)
# ---------------------------------------------------------------------------
echo "Starting ngrok (tunneling port 8001)..."
nohup ngrok http 8001 > logs/ngrok.log 2>&1 &
echo "ngrok:$!" >> "$PID_FILE"

# ---------------------------------------------------------------------------
# 7. Dashboard (Vite dev server)
# ---------------------------------------------------------------------------
echo "Starting dashboard..."
(cd dashboard/hr-pipeline && nohup npm run dev > "$REPO_ROOT/logs/dashboard.log" 2>&1 &)
echo "dashboard:background" >> "$PID_FILE"

# ---------------------------------------------------------------------------
# Wait a moment for ngrok to establish its tunnel, then print the URL
# ---------------------------------------------------------------------------
echo ""
echo "Waiting for ngrok tunnel..."
sleep 3

NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data['tunnels'][0]['public_url'])
except Exception:
    print('COULD_NOT_FETCH')
")

echo ""
echo "=================================================================="
echo "All services started. Logs are in ./logs/"
echo ""
echo "  Main app:      http://localhost:8000/docs"
echo "  Webhook app:   http://localhost:8001/health"
echo "  Dashboard:     http://localhost:5173"
echo "  ngrok URL:     $NGROK_URL"
echo ""
if [ "$NGROK_URL" != "COULD_NOT_FETCH" ]; then
  echo "  >>> If this ngrok URL is NEW, update Twilio's webhook URL to:"
  echo "  >>> $NGROK_URL/webhooks/whatsapp/inbound"
else
  echo "  Could not auto-fetch ngrok URL -- check http://127.0.0.1:4040 manually."
fi
echo ""
echo "To stop everything, run: bash scripts/stop_all.sh"
echo "=================================================================="
