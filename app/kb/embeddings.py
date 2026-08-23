"""Local TF-IDF vectorization for retrieval.

No embedding API dependency: for a small, curated corpus (a few dozen
chunks), TF-IDF cosine similarity retrieves relevant rule text reliably and
keeps the demo runnable offline / without extra API keys. Swapping in a
hosted embedding model later is a drop-in change behind `Vectorizer`.
"""

import math
import re
from collections import Counter

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A small, curated corpus makes IDF alone a weak discriminator: generic
# high-frequency words (uk, visa, visit...) still dominate cosine similarity
# unless filtered outright. Stopwords removed at tokenize time, not scored
# down, so distinctive terms (money, bank, refusal...) drive ranking.
_STOPWORDS = frozenset(
    """
    a an the this that these those and or but if then than so as of to in on
    at by for with without from into onto up down out over under again
    further once here there when where why how all any both each few more
    most other some such no nor not only own same too very s t can will just
    don should now is are was were be been being have has had do does did
    doing would could shall might must i you he she it we they me him her
    us them my your his its our their what which who whom
    """.split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class Vectorizer:
    def __init__(self, documents: list[str]):
        self._doc_freq: Counter[str] = Counter()
        tokenized = [tokenize(doc) for doc in documents]
        for tokens in tokenized:
            for term in set(tokens):
                self._doc_freq[term] += 1
        self._n_docs = len(documents)
        self._vocab = {term: i for i, term in enumerate(sorted(self._doc_freq))}
        self._idf = np.zeros(len(self._vocab))
        for term, idx in self._vocab.items():
            self._idf[idx] = math.log((1 + self._n_docs) / (1 + self._doc_freq[term])) + 1

    def transform(self, text: str) -> np.ndarray:
        vec = np.zeros(len(self._vocab))
        counts = Counter(tokenize(text))
        for term, count in counts.items():
            idx = self._vocab.get(term)
            if idx is not None:
                vec[idx] = count * self._idf[idx]
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def transform_batch(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self.transform(t) for t in texts])


def cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return matrix @ query_vec
