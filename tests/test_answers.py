import pytest
from app.answer import AnswerService, NOT_COVERED_TEXT
from app.config import settings
from app.db import get_db_connection
from app.embedder import get_embedder
from app.llm import FakeLLMProvider, LLMCallSpy
from app.retrieval import Retriever
from app.schemas import Principal
from app.vector_store import ChromaVectorStore


@pytest.mark.asyncio
async def test_llm_call_budget_enforcement():
    conn = get_db_connection(settings.SQLITE_PATH)
    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)

    spy = LLMCallSpy(FakeLLMProvider())
    service = AnswerService(conn, retriever, spy)

    principal = Principal(
        user_id="test_user",
        company_id="aurora",
        role="employee",
        display_name="Test User",
    )

    # 1. Answerable question -> exactly 1 LLM call
    resp = await service.ask(principal, "When is a security escort needed for a woman's drop?")
    assert resp.status_code if hasattr(resp, "status_code") else 200
    assert spy.call_count == 1
    assert resp.status == "answered"
    assert 1 <= len(resp.citations) <= 3

    # 2. Genuinely unrelated question where retrieval abstains -> 0 LLM calls!
    spy.call_count = 0
    resp_unrelated = await service.ask(
        principal, "quantum thermodynamics molecular biology deep space telescope"
    )
    assert resp_unrelated.status == "not_covered"
    assert resp_unrelated.answer == NOT_COVERED_TEXT
    assert len(resp_unrelated.citations) == 0
    assert spy.call_count == 0  # Abstention gate prevented LLM invocation

    conn.close()


@pytest.mark.asyncio
async def test_citation_metadata_exactness():
    conn = get_db_connection(settings.SQLITE_PATH)
    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)
    service = AnswerService(conn, retriever, FakeLLMProvider())

    principal = Principal(
        user_id="test_user",
        company_id="bluepeak",
        role="employee",
        display_name="BluePeak User",
    )

    resp = await service.ask(principal, "Who approves an airport cab ride?")
    assert resp.status == "answered"
    assert len(resp.citations) >= 1
    cit = resp.citations[0]
    assert cit.document_title == "BluePeak Corporate Commute Regulations"
    assert cit.section_id == "4"
    assert "Airport" in cit.section_title
    assert cit.chunk_id.startswith("chk_")

    conn.close()
