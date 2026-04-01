FROM python:3.10-slim

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

COPY . .
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "http.server", "8000"]
