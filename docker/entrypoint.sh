#!/bin/sh
# Prepares a container before the real command runs.
#
# Web runs migrations and collects static; workers skip both so that four
# containers starting together do not race over the same table.
set -eu

if [ "${WAIT_FOR_DB:-1}" = "1" ] && [ -n "${POSTGRES_HOST:-}" ]; then
    echo "waiting for postgres at ${POSTGRES_HOST}:${POSTGRES_PORT:-5432}"
    python - <<'PY'
import os
import socket
import sys
import time

host = os.environ["POSTGRES_HOST"]
port = int(os.environ.get("POSTGRES_PORT", "5432"))
deadline = time.time() + 90

while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            print("postgres is reachable")
            sys.exit(0)
    except OSError:
        time.sleep(1)

print("postgres did not become reachable in time", file=sys.stderr)
sys.exit(1)
PY
fi

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi

exec "$@"
