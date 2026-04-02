FROM node:20-alpine AS base

RUN addgroup -S -g 1001 appuser && adduser -S -u 1001 -G appuser appuser

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build || true
RUN mkdir -p /tmp/app && chown -R appuser:appuser /app /tmp/app

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER 1001

EXPOSE 3000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["npm", "run", "start"]
