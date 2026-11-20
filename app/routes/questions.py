from fastapi import APIRouter, Depends, Response
from app.answer import AnswerService
from app.auth import get_current_principal
from app.config import settings
from app.db import get_db_connection
from app.embedder import get_embedder
from app.llm import get_llm_provider
from app.retrieval import Retriever
from app.schemas import AskRequest, AskResponse, Principal
from app.vector_store import ChromaVectorStore

router = APIRouter(prefix="/api/v1", tags=["Questions"])


def get_db():
    conn = get_db_connection(settings.SQLITE_PATH)
    try:
        yield conn
    finally:
        conn.close()


def get_answer_service(db=Depends(get_db)) -> AnswerService:
    vector_store = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vector_store, embedder)
    llm = get_llm_provider()
    return AnswerService(db, retriever, llm)


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a transport policy question",
)
async def ask_question(
    payload: AskRequest,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    answer_service: AnswerService = Depends(get_answer_service),
):
    """
    Query authenticated company's active transport policy.
    No tenant, version, or collection override accepted.
    Spec §10 and §11.2.
    """
    result = await answer_service.ask(
        principal=principal,
        question=payload.question,
    )
    response.headers["X-Request-ID"] = result.request_id
    response.headers["Server-Timing"] = (
        f"retrieval;dur={result.timing_ms.retrieval}, "
        f"llm;dur={result.timing_ms.llm}, "
        f"total;dur={result.timing_ms.total}"
    )
    return result
