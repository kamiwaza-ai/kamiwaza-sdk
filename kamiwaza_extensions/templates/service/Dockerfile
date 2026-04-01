FROM python:3.10-slim

RUN groupadd -r -g 1001 appuser && useradd -r -u 1001 -g appuser -d /app appuser

WORKDIR /app

COPY . .
RUN chown -R appuser:appuser /app

USER 1001

EXPOSE 8000

CMD ["python", "-m", "http.server", "8000"]
