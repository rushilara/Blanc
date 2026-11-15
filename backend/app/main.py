"""FastAPI application entry point. Run with `uvicorn app.main:app` from `backend/`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis_llm import make_analysis_llm
from app.auth import require_session
from app.config import Settings, get_settings
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import make_embedder
from app.retrieval import RetrievalConfig
from app.routers import analyses, boards, cases, records, reports, search, timeline


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The DB is opened at startup, not import, so importing the app never
        # touches the developer's database.
        engine = make_engine(settings.DATABASE_URL)
        init_db(engine, settings.EMBEDDING_DIM)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        # Lazy: no network or key needed until something embeds.
        app.state.embedder = make_embedder(settings)
        app.state.analysis_llm = make_analysis_llm(settings)
        app.state.retrieval_config = RetrievalConfig.from_settings(settings)
        app.state.evidence_root = settings.EVIDENCE_ROOT
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Blanc API", version="0.1.0", lifespan=lifespan)

    if settings.BLANC_AUTH_SECRET is not None:
        # Added before CORS so CORS wraps it: a 401 still carries the CORS headers the
        # browser needs to read it.
        app.middleware("http")(require_session(settings.BLANC_AUTH_SECRET.get_secret_value()))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Frontend (:3000) reads these on cross-origin /media responses.
        expose_headers=["X-Hash-Verified", "X-Content-SHA256", "X-Source-Path"],
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        """Liveness check plus a non-secret config summary (never returns keys)."""
        return {"status": "ok", "config": settings.summary()}

    app.include_router(cases.router)
    app.include_router(records.router)
    app.include_router(search.router)
    app.include_router(boards.router)
    app.include_router(analyses.router)
    app.include_router(timeline.router)
    app.include_router(reports.router)

    return app


app = create_app()
