"""FastAPI application entry point. Run with `uvicorn app.main:app` from `backend/`."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Blanc API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        """Liveness check plus a non-secret config summary (never returns keys)."""
        return {"status": "ok", "config": settings.summary()}

    return app


app = create_app()
