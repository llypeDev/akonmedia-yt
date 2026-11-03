#!/bin/sh
# Renders the nginx configuration from the environment and starts the ingest
# server in the foreground.
set -eu

: "${LIVE_HOOK_SECRET:?LIVE_HOOK_SECRET must be set, to the same value the portal uses}"
export LIVE_HOOK_SECRET
export LIVE_PORTAL_URL="${LIVE_PORTAL_URL:-http://web:8000}"
export LIVE_DATA_DIR="${LIVE_DATA_DIR:-/var/lib/akon-live}"
# Docker's embedded DNS; any resolver works when LIVE_PORTAL_URL is an IP.
export LIVE_RESOLVER="${LIVE_RESOLVER:-127.0.0.11}"
export LIVE_NGINX_USER="${LIVE_NGINX_USER:-app}"

mkdir -p "$LIVE_DATA_DIR/hls" "$LIVE_DATA_DIR/recordings" "$LIVE_DATA_DIR/tmp"
chown "$LIVE_NGINX_USER" "$LIVE_DATA_DIR/hls" "$LIVE_DATA_DIR/recordings" "$LIVE_DATA_DIR/tmp" 2>/dev/null || true

CONF="${LIVE_NGINX_CONF:-/etc/nginx/nginx.conf}"
TEMPLATE="${LIVE_NGINX_TEMPLATE:-/etc/nginx/akon-live.conf.template}"
envsubst '${LIVE_HOOK_SECRET} ${LIVE_PORTAL_URL} ${LIVE_DATA_DIR} ${LIVE_RESOLVER} ${LIVE_NGINX_USER}' \
  < "$TEMPLATE" > "$CONF"

nginx -t -q -e stderr -c "$CONF"
exec nginx -e stderr -c "$CONF" -g 'daemon off;'
