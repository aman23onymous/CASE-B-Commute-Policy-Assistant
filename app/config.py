import os
from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    DEMO_MODE: bool = True
    JWT_SECRET: str = "demo-secret-key-at-least-32-chars-long-for-hs256!"
    JWT_ISSUER: str = "commute-policy-demo"
    JWT_AUDIENCE: str = "commute-policy-api"
    JWT_TTL_SECONDS: int = 3600

    SQLITE_PATH: str = "data/registry.sqlite3"
    CHROMA_PATH: str = "data/chroma"

    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    LLM_PROVIDER: Literal["fake", "groq", "gemini"] = "fake"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-8b-instant"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"

    RETRIEVAL_MODE: Literal["baseline", "hybrid"] = "baseline"
    RETRIEVAL_TIMEOUT_SECONDS: float = 3.0
    LLM_TIMEOUT_SECONDS: float = 4.0
    REQUEST_TIMEOUT_SECONDS: float = 6.0
    MAX_POLICY_BYTES: int = 1048576  # 1 MiB

    # Semantic similarity gating (cosine distance threshold in Chroma, where dist = 1 - cos_sim)
    # distance <= 0.80 allows paraphrased and adversarially-prefixed queries while rejecting unrelated queries
    MAX_RETRIEVAL_DISTANCE: float = 0.80

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def ensure_directories(self) -> None:
        """Ensure parent directories for sqlite and chroma exist."""
        sqlite_file = Path(self.SQLITE_PATH)
        sqlite_file.parent.mkdir(parents=True, exist_ok=True)
        chroma_dir = Path(self.CHROMA_PATH)
        chroma_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
