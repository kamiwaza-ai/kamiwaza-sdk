FROM node:20-alpine AS base

RUN addgroup -S -g 1001 appuser && adduser -S -u 1001 -G appuser appuser

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN chown -R appuser:appuser /app

USER 1001

EXPOSE 3000

CMD ["npm", "run", "dev"]
