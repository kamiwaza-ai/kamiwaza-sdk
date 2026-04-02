#!/bin/sh
set -e

export NEXT_TELEMETRY_DISABLED=1

# If running under a path prefix, rebuild with basePath
if [ -n "$KAMIWAZA_APP_PATH" ]; then
    echo "Building Next.js for base path: $KAMIWAZA_APP_PATH"
    mkdir -p /tmp/app
    cp -r /app/. /tmp/app/
    cd /tmp/app
    npm run build
    exec npm run start
fi

cd /app
exec "$@"
