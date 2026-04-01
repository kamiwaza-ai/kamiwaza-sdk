FROM node:20-alpine AS base

RUN addgroup -S appuser && adduser -S appuser -G appuser

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 3000

CMD ["npm", "run", "dev"]
