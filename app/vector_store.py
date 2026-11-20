import os
import re
import uuid
from typing import Any, Dict, List, Optional
import chromadb
from chromadb.config import Settings as ChromaSettings

from app.chunking import PolicyChunk
from app.config import settings
from app.embedder import Embedder


class VectorStoreError(Exception):
    """Base exception for vector store operations."""
    pass


def sanitize_collection_name(company_id: str, version: int) -> str:
    """Generate safe, RFC-compliant Chroma collection name."""
    clean_company = re.sub(r"[^a-zA-Z0-9_-]", "", company_id)
    short_uid = uuid.uuid4().hex[:8]
    # Chroma collection names: 3-63 chars, start/end alphanumeric
    return f"policy_{clean_company}_v{version}_{short_uid}"


class ChromaVectorStore:
    def __init__(self, chroma_path: Optional[str] = None):
        self.chroma_path = chroma_path or settings.CHROMA_PATH
        os.makedirs(self.chroma_path, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=self.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False, is_persistent=True),
        )

    def get_or_create_collection(self, collection_name: str):
        """Get or create collection with cosine distance metric."""
        return self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def populate_staging_collection(
        self,
        collection_name: str,
        chunks: List[PolicyChunk],
        embedder: Embedder,
    ) -> int:
        """
        Embed and insert all chunks into a staging collection.
        Returns total inserted chunk count.
        """
        if not chunks:
            raise VectorStoreError("Cannot populate staging collection with 0 chunks.")

        collection = self.get_or_create_collection(collection_name)

        texts = [c.text for c in chunks]
        embeddings = embedder.embed_texts(texts)
        ids = [c.chunk_id for c in chunks]
        metadatas = [c.to_metadata() for c in chunks]

        # Insert chunks
        collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=texts,
        )

        # Verification step (§6 step 5): read back and verify count
        readback_count = collection.count()
        if readback_count != len(chunks):
            raise VectorStoreError(
                f"Collection count mismatch during verification: expected {len(chunks)}, got {readback_count}"
            )

        return readback_count

    def search_active_collection(
        self,
        collection_name: str,
        query_text: str,
        expected_company_id: str,
        expected_version_id: str,
        embedder: Embedder,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Query ONLY the active Chroma collection for this tenant + version snapshot.
        Enforces both server-owned where clause and post-retrieval verification.
        Spec §5.2, §7.1, §9.1.
        """
        try:
            collection = self.client.get_collection(collection_name)
        except Exception as e:
            raise VectorStoreError(
                f"Active collection '{collection_name}' not found: {str(e)}"
            )

        query_embedding = embedder.embed_query(query_text)

        # Scoped search with strict where filter
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, max(1, collection.count())),
            where={
                "$and": [
                    {"company_id": {"$eq": expected_company_id}},
                    {"policy_version_id": {"$eq": expected_version_id}},
                ]
            },
            include=["documents", "metadatas", "distances"],
        )

        candidates: List[Dict[str, Any]] = []

        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i, chunk_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            dist = distances[i] if i < len(distances) else 1.0
            doc = docs[i] if i < len(docs) else ""

            # Defense-in-depth: invariant check after retrieval
            if meta.get("company_id") != expected_company_id:
                # Security anomaly: discard and do not return
                continue
            if meta.get("policy_version_id") != expected_version_id:
                # Version anomaly: discard
                continue

            candidates.append({
                "chunk_id": chunk_id,
                "text": doc,
                "metadata": meta,
                "distance": dist,
                "similarity": 1.0 - dist,
            })

        return candidates

    def get_all_chunks_for_lexical(
        self,
        collection_name: str,
        expected_company_id: str,
        expected_version_id: str,
    ) -> List[Dict[str, Any]]:
        """Retrieve all chunks for this specific active collection for hybrid BM25 scoring."""
        try:
            collection = self.client.get_collection(collection_name)
            res = collection.get(
                where={
                    "$and": [
                        {"company_id": {"$eq": expected_company_id}},
                        {"policy_version_id": {"$eq": expected_version_id}},
                    ]
                },
                include=["documents", "metadatas"],
            )
            chunks = []
            ids = res.get("ids", [])
            docs = res.get("documents", [])
            metas = res.get("metadatas", [])
            for i, cid in enumerate(ids):
                meta = metas[i] if i < len(metas) else {}
                if meta.get("company_id") == expected_company_id and meta.get("policy_version_id") == expected_version_id:
                    chunks.append({
                        "chunk_id": cid,
                        "text": docs[i] if i < len(docs) else "",
                        "metadata": meta,
                    })
            return chunks
        except Exception:
            return []

    def delete_collection_safely(self, collection_name: str) -> None:
        """Best-effort deletion of staging or temporary collection."""
        try:
            self.client.delete_collection(name=collection_name)
        except Exception:
            pass
