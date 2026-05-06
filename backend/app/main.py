from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings


settings = get_settings()

app = FastAPI(title="Webmail MVP API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "status": "ok",
            "service": settings.app_name,
            "environment": settings.app_env,
        },
        "error": None,
    }


@app.get("/api/ready", tags=["health"])
def ready() -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "status": "ready",
            "dependencies": {
                "postgres": "configured",
                "redis": "configured",
            },
        },
        "error": None,
    }
