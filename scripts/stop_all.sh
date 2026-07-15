#!/usr/bin/env bash
#
# scripts/stop_all.sh
#
# Stops every process started by start_all.sh, using the PIDs recorded
# in .pids. Does not stop Redis (Docker container is left running since
# it's cheap to leave up and other things may depend on it).
#
# Usage:
#   bash scripts/stop_all.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$REPO_ROOT/.pids"

if [ ! -f "$PID_FILE" ]; then
  echo "No .pids file found -- nothing to stop (or start_all.sh was never run)."
  exit 0
fi

echo "Stopping services..."

while IFS=: read -r name pid; do
  if [ "$pid" == "background" ]; then
    continue
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo "  Stopping $name (PID $pid)..."
    kill "$pid" 2>/dev/null
  else
    echo "  $name (PID $pid) already stopped."
  fi
done < "$PID_FILE"

DASHBOARD_PID=$(lsof -ti :5173 2>/dev/null)
if [ -n "$DASHBOARD_PID" ]; then
  echo "  Stopping dashboard (PID $DASHBOARD_PID)..."
  kill "$DASHBOARD_PID" 2>/dev/null
fi

rm -f "$PID_FILE"
echo "Done. (Redis was left running -- stop manually with 'docker stop whatsapp-recruiting-agent-redis-1' if you want it fully down.)"
