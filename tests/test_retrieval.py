import pytest
from app.config import settings
from app.db import get_active_policy_snapshot, get_db_connection
from app.embedder import get_embedder
from app.retrieval import Retriever
from app.vector_store import ChromaVectorStore


def test_scoped_retrieval_baseline():
    conn = get_db_connection(settings.SQLITE_PATH)
    active = get_active_policy_snapshot(conn, "aurora")
    conn.close()
    assert active is not None

    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)

    results = retriever.retrieve(
        query="When is a security escort needed?",
        company_id="aurora",
        policy_version_id=active["policy_version_id"],
        collection_name=active["collection_name"],
        mode="baseline",
        max_citations=3,
    )
    assert len(results) >= 1
    # Check that retrieved chunk has section_id 2
    sec_ids = [r["metadata"]["section_id"] for r in results]
    assert "2" in sec_ids
    # Verify strict invariant
    for r in results:
        assert r["metadata"]["company_id"] == "aurora"
        assert r["metadata"]["policy_version_id"] == active["policy_version_id"]


def test_scoped_retrieval_hybrid():
    conn = get_db_connection(settings.SQLITE_PATH)
    active = get_active_policy_snapshot(conn, "bluepeak")
    conn.close()
    assert active is not None

    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)

    results = retriever.retrieve(
        query="Who approves an airport cab ride?",
        company_id="bluepeak",
        policy_version_id=active["policy_version_id"],
        collection_name=active["collection_name"],
        mode="hybrid",
        max_citations=3,
    )
    assert len(results) >= 1
    sec_ids = [r["metadata"]["section_id"] for r in results]
    assert "4" in sec_ids


def test_relevance_gate_abstains_on_unrelated_query():
    conn = get_db_connection(settings.SQLITE_PATH)
    active = get_active_policy_snapshot(conn, "aurora")
    conn.close()

    vs = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vs, embedder)

    # Completely alien query
    results = retriever.retrieve(
        query="quantum entanglement thermodynamics galactic nebula astrophysics",
        company_id="aurora",
        policy_version_id=active["policy_version_id"],
        collection_name=active["collection_name"],
        mode="baseline",
        max_citations=3,
    )
    # Relevance gate must filter it out
    assert len(results) == 0
