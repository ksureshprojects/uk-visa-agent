"""Local sentence-embedding vectorization for retrieval, via fastembed.

Runs a small quantized transformer fully on-device (onnxruntime, not
PyTorch) — no API key and no network calls at inference time, only a
one-off model download that's then cached to disk. This ranks chunks by
semantic similarity rather than literal word overlap, so colloquial or
synonymous phrasing (e.g. "my girlfriend" vs. the KB's "partner") still
retrieves the right chunk. Swapping the model is a one-line change to
_MODEL_NAME.
"""

from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

from app.config import EMBEDDING_MODEL_CACHE_DIR

_MODEL_NAME = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding(model_name=_MODEL_NAME, cache_dir=str(EMBEDDING_MODEL_CACHE_DIR))


class Vectorizer:
    def transform(self, text: str) -> np.ndarray:
        """Embed a query. BGE models are trained asymmetrically: queries
        need the retrieval-instruction prefix that query_embed applies;
        passages (transform_batch) are embedded plain."""
        return next(_model().query_embed([text]))

    def transform_batch(self, texts: list[str]) -> np.ndarray:
        return np.array(list(_model().embed(texts)))


def cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    # BGE embeddings are L2-normalized, so the dot product is already cosine similarity.
    return matrix @ query_vec
