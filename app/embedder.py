import hashlib
import math
from typing import List, Protocol
from app.config import settings


class Embedder(Protocol):
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        ...

    def embed_query(self, query: str) -> List[float]:
        ...


class FakeEmbedder:
    """
    Deterministic offline embedder for tests and CI.
    Generates repeatable 384-dimensional unit-length vectors from text tokens.
    Requires no external downloads, network access, or PyTorch inference.
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def _hash_vector(self, text: str) -> List[float]:
        tokens = text.lower().split()
        vec = [0.0] * self.dimension
        if not tokens:
            tokens = ["empty"]
        for token in tokens:
            token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = token_hash % self.dimension
            val = ((token_hash >> 16) % 1000) / 1000.0 - 0.5
            vec[idx] += val

        # Normalize to unit sphere (L2 norm)
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        else:
            vec[0] = 1.0
        return vec

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [self._hash_vector(t) for t in texts]

    def embed_query(self, query: str) -> List[float]:
        return self._hash_vector(query)


class SentenceTransformerEmbedder:
    """Production embedder using sentence-transformers (all-MiniLM-L6-v2)."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    def embed_query(self, query: str) -> List[float]:
        embedding = self.model.encode(query, normalize_embeddings=True)
        return embedding.tolist()


_embedder_instance = None


def get_embedder(force_fake: bool = False) -> Embedder:
    """Retrieve or initialize embedder singleton based on settings and arguments."""
    global _embedder_instance
    if force_fake:
        return FakeEmbedder()

    if _embedder_instance is None:
        try:
            _embedder_instance = SentenceTransformerEmbedder(settings.EMBEDDING_MODEL)
        except Exception:
            # Fall back to FakeEmbedder if sentence-transformers model cannot be loaded
            _embedder_instance = FakeEmbedder()
    return _embedder_instance
