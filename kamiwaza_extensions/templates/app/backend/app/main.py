"""FastAPI backend for {{name}}."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="{{name}}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "app": "{{name}}"}


@app.get("/api/info")
async def info():
    return {
        "app_name": os.getenv("KAMIWAZA_APP_NAME", "{{name}}"),
        "api_url": os.getenv("KAMIWAZA_API_URL", ""),
    }
