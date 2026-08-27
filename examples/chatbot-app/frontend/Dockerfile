# App Garden dual-artifact Next runtime.
#
# The image ships TWO prebuilt standalone artifacts — a native no-basePath
# build (port routing) and a sentinel-basePath build (path routing) — plus a
# relocation manifest. At container start the boot entrypoint either runs the
# port artifact as-is or relocates the sentinel artifact to the deployment's
# KAMIWAZA_APP_PATH in /tmp (seconds, tens of MB). There is NO `next build`,
# npm, or source tree at runtime: the spawn-time rebuild is gone.

FROM node:20-alpine AS base
RUN addgroup -S -g 1001 appuser && adduser -S -u 1001 -G appuser appuser
WORKDIR /app

# ---------------------------------------------------------------------------
FROM base AS deps
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

# ---------------------------------------------------------------------------
# Local development only (`kz-ext dev local` selects this target via a
# compose override). Never published.
FROM deps AS dev
COPY . .
EXPOSE 3000
ENV NEXT_TELEMETRY_DISABLED=1
CMD ["npx", "next", "dev", "--hostname", "0.0.0.0"]

# ---------------------------------------------------------------------------
FROM deps AS build-port
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 \
    KZ_NEXT_BUILD_VARIANT=port
RUN npm run build \
    && mkdir -p /out/port/.next/static \
    && cp -R .next/standalone/. /out/port/ \
    && cp -R .next/static/. /out/port/.next/static/ \
    && { [ ! -d public ] || cp -R public /out/port/public; }

# ---------------------------------------------------------------------------
FROM deps AS build-path
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 \
    KZ_NEXT_BUILD_VARIANT=path
RUN npm run build \
    && mkdir -p /out/path/.next/static \
    && cp -R .next/standalone/. /out/path/ \
    && cp -R .next/static/. /out/path/.next/static/ \
    && { [ ! -d public ] || cp -R public /out/path/public; }

# ---------------------------------------------------------------------------
# Index the assembled path artifact. Fails the image build if the sentinel
# appears anywhere it cannot be safely relocated (binary files,
# node_modules, source maps) or is missing from mandatory roles.
FROM base AS runtime-assembly
COPY --from=build-port /out/port /app/runtime/port
COPY --from=build-path /out/path /app/runtime/path
COPY --from=deps /app/node_modules/@kamiwaza-ai/extensions-lib/scripts /app/runtime-tools
RUN node /app/runtime-tools/index-next-runtime.mjs \
    --root /app/runtime/path \
    --sentinel /__KZ_RUNTIME_BASE_7F3A91C2__ \
    --next-version "$(node -p "require('/app/runtime/path/node_modules/next/package.json').version")" \
    --output /app/runtime/kz-next-relocations.json

# ---------------------------------------------------------------------------
# Final runner: artifacts + stdlib runtime tools. No source, no npm, no dev
# dependencies. Read-only rootfs compatible; /tmp is the only writable path.
FROM node:20-alpine AS runner
RUN addgroup -S -g 1001 appuser && adduser -S -u 1001 -G appuser appuser
WORKDIR /app
COPY --from=runtime-assembly --chown=1001:1001 /app/runtime /app/runtime
COPY --from=runtime-assembly --chown=1001:1001 /app/runtime-tools /app/runtime-tools

USER 1001
EXPOSE 3000
ENV PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# Health check resolves the same routing mode as the entrypoint and probes
# the frontend /health route under the deployment prefix when in path mode.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD node --input-type=module -e "import{resolveRoutingMode}from'/app/runtime-tools/start-next-runtime.mjs';import http from'node:http';const p=resolveRoutingMode(process.env).appPath;http.get('http://127.0.0.1:'+(process.env.PORT||3000)+p+'/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"

ENTRYPOINT ["node", "/app/runtime-tools/start-next-runtime.mjs"]
