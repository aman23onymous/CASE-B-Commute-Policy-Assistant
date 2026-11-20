import asyncio
import pytest
from fastapi import HTTPException
from app.answer import AnswerService, RETRY_ERROR_TEXT, NOT_COVERED_TEXT
from app.config import settings
from app.db import get_db_connection
from app.embedder import get_embedder
from app.llm import LLMError
from app.retrieval import Retriever
from app.schemas import Principal
from app.vector_store import ChromaVectorStore


class TimeoutLLMProvider:
    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        raise LLMError("LLM simulated network read timeout")


class MalformedJSONLLMProvider:
    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        return "This is plain text and definitely NOT valid JSON."


class UnknownCitationLLMProvider:
    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        return '{"status": "answered", "answer": "Fabricated fact", "citation_ids": ["C999"]}'


@pytest.mark.asyncio
async def test_llm_provider_error_raises_retryable_503():
    conn = get_db_connection(settings.SQLITE_PATH)
    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)
    service = AnswerService(conn, retriever, TimeoutLLMProvider())

    principal = Principal(user_id="u", company_id="aurora", role="employee", display_name="U")

    with pytest.raises(HTTPException) as exc_info:
        await service.ask(principal, "When is a security escort needed?")
    assert exc_info.value.status_code in (503, 504)
    assert exc_info.value.detail == RETRY_ERROR_TEXT
    conn.close()


@pytest.mark.asyncio
async def test_malformed_llm_json_fails_closed():
    conn = get_db_connection(settings.SQLITE_PATH)
    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)
    service = AnswerService(conn, retriever, MalformedJSONLLMProvider())

    principal = Principal(user_id="u", company_id="aurora", role="employee", display_name="U")

    resp = await service.ask(principal, "When is a security escort needed?")
    assert resp.status == "not_covered"
    assert resp.answer == NOT_COVERED_TEXT
    assert len(resp.citations) == 0
    conn.close()


@pytest.mark.asyncio
async def test_unknown_citation_ids_fail_closed():
    conn = get_db_connection(settings.SQLITE_PATH)
    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)
    service = AnswerService(conn, retriever, UnknownCitationLLMProvider())

    principal = Principal(user_id="u", company_id="aurora", role="employee", display_name="U")

    resp = await service.ask(principal, "When is a security escort needed?")
    assert resp.status == "not_covered"
    assert resp.answer == NOT_COVERED_TEXT
    assert len(resp.citations) == 0
    conn.close()


def test_question_input_limits(client, aurora_token):
    # Empty question
    r_empty = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": ""},
    )
    assert r_empty.status_code == 422

    # Excessively long question (> 1000 characters)
    r_long = client.post(
        "/api/v1/ask",
        headers={"Authorization": f"Bearer {aurora_token}"},
        json={"question": "a" * 1005},
    )
    assert r_long.status_code == 422
