#!/usr/bin/env bash
# Runs the app behind a temporary Cloudflare quick tunnel so it's reachable
# from a public, hard-to-guess trycloudflare.com URL without opening any
# firewall ports. Useful for testing from a Google Cloud VM (or anywhere
# without a public IP) since it makes an outbound-only connection.
#
# The app has no auth (see README "Known limitations"), so the tunnel URL
# is effectively an open door to anyone who has it — kill this script
# (Ctrl+C) when you're done testing.
#
# Usage:
#   ANTHROPIC_API_KEY=sk-ant-... scripts/start_tunnel.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$ROOT_DIR/.bin"
CLOUDFLARED="$BIN_DIR/cloudflared"
PORT="${PORT:-8000}"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "Warning: ANTHROPIC_API_KEY is not set. The server will start but" >&2
  echo "         sending a chat message will fail against the Anthropic API." >&2
fi

if [[ ! -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
  echo "Error: $ROOT_DIR/.venv not found. Run this first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1 && [[ ! -x "$CLOUDFLARED" ]]; then
  echo "Installing cloudflared to $CLOUDFLARED ..."
  mkdir -p "$BIN_DIR"
  arch="$(uname -m)"
  case "$arch" in
    x86_64) cf_arch="amd64" ;;
    aarch64) cf_arch="arm64" ;;
    *) echo "Error: unsupported architecture $arch" >&2; exit 1 ;;
  esac
  curl -fL -o "$CLOUDFLARED" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cf_arch}"
  chmod +x "$CLOUDFLARED"
fi

if command -v cloudflared >/dev/null 2>&1; then
  CLOUDFLARED="cloudflared"
fi

UVICORN_PID=""
cleanup() {
  if [[ -n "$UVICORN_PID" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    echo "Stopping uvicorn (pid $UVICORN_PID) ..."
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting uvicorn on 127.0.0.1:$PORT ..."
"$ROOT_DIR/.venv/bin/uvicorn" app.api.main:app --host 127.0.0.1 --port "$PORT" \
  --app-dir "$ROOT_DIR" &
UVICORN_PID=$!

for _ in $(seq 1 30); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
    break
  fi
  sleep 1
done

echo "Starting cloudflared quick tunnel -> http://127.0.0.1:$PORT ..."
echo "(the public URL will be printed below — look for *.trycloudflare.com)"
"$CLOUDFLARED" tunnel --url "http://127.0.0.1:$PORT"
