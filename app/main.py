import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import get_db_connection, init_db
from app.routes import auth, policies, questions
from app.vector_store import ChromaVectorStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure directories exist, SQLite schema initialized, and embedder pre-warmed
    settings.ensure_directories()
    init_db(settings.SQLITE_PATH)
    try:
        from app.embedder import get_embedder
        emb = get_embedder()
        emb.embed_query("warmup commute policy")
    except Exception:
        pass
    yield
    # Shutdown


app = FastAPI(
    title="Commute Policy Assistant",
    description="Multi-tenant Commute Policy Assistant with strict tenant isolation and atomic ingestion",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Normalize transient and authentication error payloads without leaking internals."""
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    if exc.status_code in (status.HTTP_503_SERVICE_UNAVAILABLE, status.HTTP_504_GATEWAY_TIMEOUT):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "request_id": req_id,
                "status": "retryable_error",
                "message": exc.detail or "We couldn't check the policy right now. Please try again shortly.",
            },
            headers={"X-Request-ID": req_id},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={"X-Request-ID": req_id},
    )


# Mount API routers
app.include_router(auth.router)
app.include_router(questions.router)
app.include_router(policies.router)


# Health checks (Spec §11.3)
@app.get("/health/live", tags=["Health"])
def health_live():
    return {"status": "alive"}


@app.get("/health/ready", tags=["Health"])
def health_ready():
    try:
        conn = get_db_connection(settings.SQLITE_PATH)
        conn.execute("SELECT 1;").fetchone()
        conn.close()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database not ready: {str(e)}",
        )

    try:
        vs = ChromaVectorStore(settings.CHROMA_PATH)
        _ = vs.client.heartbeat()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Vector store not ready: {str(e)}",
        )

    return {
        "status": "ready",
        "database": "ok",
        "vector_store": "ok",
        "retrieval_mode": settings.RETRIEVAL_MODE,
        "llm_provider": settings.LLM_PROVIDER,
    }


# Static frontend UI (Spec §13)
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", tags=["UI"], include_in_schema=False)
    async def serve_ui():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Commute Policy Assistant API. Docs available at /docs"}
