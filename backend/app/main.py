"""FastAPI application entry point. Run with `uvicorn app.main:app` from `backend/`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis_llm import make_analysis_llm
from app.config import Settings, get_settings
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import make_embedder
from app.retrieval import RetrievalConfig
from app.routers import boards, cases, records, search


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
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Blanc API", version="0.1.0", lifespan=lifespan)

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

    app.include_router(cases.router)
    app.include_router(records.router)
    app.include_router(search.router)
    app.include_router(boards.router)

    return app


app = create_app()
