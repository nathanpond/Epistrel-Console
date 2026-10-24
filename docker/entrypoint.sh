#!/bin/sh
# Container entrypoint. No arguments or `serve`: migrate the Console's own database, then exec
# gunicorn as PID 1 (it handles SIGTERM). --preload loads the WSGI module in the master, so a
# missing or invalid CONSOLE_ENGINE_URL or a missing CONSOLE_ENGINE_SERVICE_KEY stops the container
# instead of looping workers.
# --no-control-socket: gunicorn 26's control interface is unused, and /app is not writable.
# Anything else is executed as given, so `docker run epistrel-console python manage.py ...` works.
set -eu
cd /app
if [ "$#" -eq 0 ]; then
    set -- serve
fi
if [ "$1" = "serve" ]; then
    if [ "$#" -ne 1 ]; then
        echo "usage: entrypoint.sh [serve | <command> ...] (serve takes no arguments)" >&2
        exit 2
    fi
    python manage.py migrate --noinput
    exec gunicorn epistrel_console.wsgi:application \
        --preload \
        --bind 0.0.0.0:7739 \
        --workers 2 \
        --timeout 30 \
        --worker-tmp-dir /dev/shm \
        --no-control-socket \
        --access-logfile - \
        --error-logfile -
fi
exec "$@"
