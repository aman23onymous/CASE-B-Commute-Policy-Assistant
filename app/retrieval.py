import math
import re
from typing import Any, Dict, List, Optional
from app.config import settings
from app.embedder import Embedder
from app.vector_store import ChromaVectorStore


class LexicalBM25Index:
    """Lightweight in-memory BM25 index scoped strictly to an active policy snapshot."""

    def __init__(self, chunks: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.chunks = chunks
        self.doc_len = [len(self._tokenize(c["text"])) for c in chunks]
        self.avg_doc_len = sum(self.doc_len) / max(1, len(self.doc_len))
        self.df: Dict[str, int] = {}
        self.doc_tfs: List[Dict[str, int]] = []

        for c in chunks:
            tokens = self._tokenize(c["text"])
            tf: Dict[str, int] = {}
            seen = set()
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
                if t not in seen:
                    self.df[t] = self.df.get(t, 0) + 1
                    seen.add(t)
            self.doc_tfs.append(tf)

    def _tokenize(self, text: str) -> List[str]:
        return [w.lower() for w in re.findall(r"\b\w+\b", text)]

    def score(self, query: str) -> List[float]:
        q_tokens = self._tokenize(query)
        num_docs = len(self.chunks)
        scores = [0.0] * num_docs

        for token in q_tokens:
            if token not in self.df:
                continue
            df_val = self.df[token]
            idf = math.log((num_docs - df_val + 0.5) / (df_val + 0.5) + 1.0)
            for i, doc_tf in enumerate(self.doc_tfs):
                f = doc_tf.get(token, 0)
                if f > 0:
                    denom = f + self.k1 * (1.0 - self.b + self.b * (self.doc_len[i] / max(1.0, self.avg_doc_len)))
                    scores[i] += idf * (f * (self.k1 + 1.0)) / denom

        return scores


# Tenant and version-keyed lexical index cache
_lexical_cache: Dict[str, LexicalBM25Index] = {}


def get_lexical_index(
    vector_store: ChromaVectorStore,
    company_id: str,
    policy_version_id: str,
    collection_name: str,
) -> LexicalBM25Index:
    cache_key = f"{company_id}:{policy_version_id}"
    if cache_key not in _lexical_cache:
        chunks = vector_store.get_all_chunks_for_lexical(
            collection_name=collection_name,
            expected_company_id=company_id,
            expected_version_id=policy_version_id,
        )
        _lexical_cache[cache_key] = LexicalBM25Index(chunks)
    return _lexical_cache[cache_key]


class Retriever:
    def __init__(self, vector_store: ChromaVectorStore, embedder: Embedder):
        self.vector_store = vector_store
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        company_id: str,
        policy_version_id: str,
        collection_name: str,
        mode: Optional[str] = None,
        max_citations: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve scoped policy chunks for authenticated company.
        Supports both 'baseline' and 'hybrid' modes.
        Spec §9.1 and §9.2.
        """
        active_mode = mode or settings.RETRIEVAL_MODE

        # 1. Semantic retrieval from Chroma (scoped exclusively to this collection)
        semantic_results = self.vector_store.search_active_collection(
            collection_name=collection_name,
            query_text=query,
            expected_company_id=company_id,
            expected_version_id=policy_version_id,
            embedder=self.embedder,
            n_results=5,
        )

        if not semantic_results:
            return []

        # Check relevance gate: if top semantic distance exceeds threshold
        best_dist = min(c["distance"] for c in semantic_results)
        if best_dist > settings.MAX_RETRIEVAL_DISTANCE:
            # Insufficient semantic similarity
            return []

        if active_mode == "hybrid":
            # Reciprocal Rank Fusion (RRF) between semantic and BM25
            lexical_index = get_lexical_index(
                self.vector_store, company_id, policy_version_id, collection_name
            )
            bm25_scores = lexical_index.score(query)

            # Sort BM25 chunks
            bm25_ranked = sorted(
                enumerate(bm25_scores), key=lambda x: x[1], reverse=True
            )
            bm25_rank_map: Dict[str, int] = {}
            for rank, (doc_idx, sc) in enumerate(bm25_ranked):
                if sc > 0:
                    cid = lexical_index.chunks[doc_idx]["chunk_id"]
                    bm25_rank_map[cid] = rank + 1

            # Semantic ranks
            rrf_k = 60
            scored_candidates: List[Dict[str, Any]] = []
            for s_rank, cand in enumerate(semantic_results):
                cid = cand["chunk_id"]
                score = 1.0 / (rrf_k + s_rank + 1)
                if cid in bm25_rank_map:
                    score += 1.0 / (rrf_k + bm25_rank_map[cid])
                cand_copy = dict(cand)
                cand_copy["rrf_score"] = score
                scored_candidates.append(cand_copy)

            ranked = sorted(scored_candidates, key=lambda x: x["rrf_score"], reverse=True)
            return ranked[:max_citations]

        # Baseline: sort strictly by semantic similarity (lowest distance)
        ranked = sorted(semantic_results, key=lambda x: x["distance"])
        return ranked[:max_citations]
