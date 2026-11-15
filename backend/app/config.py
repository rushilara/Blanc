"""Application settings, loaded from the environment and the repo-root `.env` file.

Real environment variables take precedence over values in `.env`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/config.py -> repo root is two levels above `app/`.
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_DIM = 768


class Settings(BaseSettings):
    """Runtime configuration. API keys are optional so the app starts without them."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Google Gemini API key, used for both the LLM and embeddings.
    GEMINI_API_KEY: SecretStr | None = None
    # LLM model. Override GEMINI_MODEL in .env to switch models.
    GEMINI_MODEL: str = "gemini-3.5-flash"

    # Embeddings: Gemini embeddings API. EMBEDDING_DIM is the output_dimensionality requested
    # from the model and the dimension of the sqlite-vec index; changing either marks stored
    # chunk vectors stale (see app/evidence_store.py: reembed_stale_chunks).
    EMBEDDING_MODEL: str = DEFAULT_EMBEDDING_MODEL
    EMBEDDING_DIM: int = Field(default=DEFAULT_EMBEDDING_DIM, ge=1, le=3072)

    # Retrieval (app/retrieval.py). Candidates: chunks pulled from each index per question.
    # Fusion: reciprocal rank fusion, score = sum(weight / (RRF_K + rank)). Evidence window:
    # at most RETRIEVAL_MAX_RECORDS records and RETRIEVAL_MAX_EXCERPT_CHARS characters of
    # excerpt text in total (~4 chars per token, so 32000 chars ~ 8000 tokens).
    RETRIEVAL_VECTOR_CANDIDATES: int = Field(default=50, ge=1, le=4096)
    RETRIEVAL_KEYWORD_CANDIDATES: int = Field(default=50, ge=1, le=4096)
    RETRIEVAL_RRF_K: int = Field(default=60, ge=0)
    RETRIEVAL_VECTOR_WEIGHT: float = Field(default=1.0, ge=0)
    RETRIEVAL_KEYWORD_WEIGHT: float = Field(default=1.0, ge=0)
    RETRIEVAL_MAX_RECORDS: int = Field(default=16, ge=1, le=200)
    RETRIEVAL_MAX_EXCERPT_CHARS: int = Field(default=32000, ge=1)
    RETRIEVAL_MAX_EXCERPTS_PER_RECORD: int = Field(default=2, ge=1)
    # The "expanded" evidence window (BLA-64): an investigator-chosen rerun that raises only
    # the record count; RETRIEVAL_MAX_EXCERPT_CHARS stays the same. Must exceed
    # RETRIEVAL_MAX_RECORDS (the "standard" window).
    RETRIEVAL_EXPANDED_MAX_RECORDS: int = Field(default=24, ge=2, le=200)

    # Storage: one SQLite file holds tables, the sqlite-vec vector index and the FTS5 index.
    DATABASE_URL: str = "sqlite:///./data/blanc.db"

    # Optional local folder of extracted original files (e.g. data/narcos), used to show a
    # record's original image. Relative paths resolve from backend/. Never a remote URL.
    EVIDENCE_ROOT: Path | None = None

    @field_validator("EVIDENCE_ROOT", mode="before")
    @classmethod
    def _blank_evidence_root_is_unset(cls, value: object) -> object:
        # `EVIDENCE_ROOT=` (as in .env.example) must mean "no images", not Path("."),
        # which would expose image files under backend/.
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    # Allowed frontend origins, comma-separated in the environment.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("GEMINI_API_KEY", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _expanded_window_is_larger(self) -> "Settings":
        if self.RETRIEVAL_EXPANDED_MAX_RECORDS <= self.RETRIEVAL_MAX_RECORDS:
            raise ValueError(
                "RETRIEVAL_EXPANDED_MAX_RECORDS must be larger than RETRIEVAL_MAX_RECORDS"
            )
        return self

    def summary(self) -> dict[str, object]:
        """Non-secret view of the configuration, safe to expose on /health."""
        return {
            "llm_provider": "gemini",
            "gemini_model": self.GEMINI_MODEL,
            "gemini_api_key_set": self.GEMINI_API_KEY is not None,
            "embedding_provider": "gemini",
            "embedding_model": self.EMBEDDING_MODEL,
            "embedding_dim": self.EMBEDDING_DIM,
            "cors_origins": self.CORS_ORIGINS,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
