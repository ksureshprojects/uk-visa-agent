from dataclasses import dataclass

from app.config import RETRIEVAL_TOP_K

from app.kb.embeddings import Vectorizer, cosine_similarity
from app.kb.loader import KBChunk, kb_version, load_chunks


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: KBChunk
    score: float


class KnowledgeStore:
    """Loads the KB corpus once and serves top-k retrieval by cosine similarity."""

    def __init__(self, kb_dir=None):
        self.chunks: list[KBChunk] = load_chunks(kb_dir)
        self.version: str = kb_version(self.chunks)
        self._vectorizer = Vectorizer([c.text for c in self.chunks])
        self._matrix = self._vectorizer.transform_batch([c.text for c in self.chunks])

    def retrieve(self, query: str, top_k: int = RETRIEVAL_TOP_K) -> list[RetrievedChunk]:
        query_vec = self._vectorizer.transform(query)
        scores = cosine_similarity(query_vec, self._matrix)
        ranked = sorted(zip(self.chunks, scores), key=lambda pair: pair[1], reverse=True)
        return [RetrievedChunk(chunk=c, score=float(s)) for c, s in ranked[:top_k] if s > 0]

    def by_citation_id(self, citation_id: str) -> KBChunk | None:
        return next((c for c in self.chunks if c.citation_id == citation_id), None)
