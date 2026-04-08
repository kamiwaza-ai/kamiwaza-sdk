FROM node:20-alpine AS base

RUN addgroup -S -g 1001 appuser && adduser -S -u 1001 -G appuser appuser

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN mkdir -p /tmp/app && chown -R appuser:appuser /app /tmp/app

USER 1001

EXPOSE 3000

# Build happens at runtime via start.mjs so KAMIWAZA_APP_PATH
# (injected by the operator) is available for Next.js basePath.
ENTRYPOINT ["node", "/app/start.mjs"]
