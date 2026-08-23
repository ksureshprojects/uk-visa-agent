import hashlib
import re
from dataclasses import dataclass

import yaml

from app.config import KB_DIR

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class KBChunk:
    citation_id: str
    source_url: str
    source_title: str
    retrieved_date: str
    text: str
    file_path: str


def _parse_file(path) -> KBChunk:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise ValueError(f"{path} is missing a YAML frontmatter block (--- ... ---)")
    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()
    for required in ("citation_id", "source_url", "source_title", "retrieved_date"):
        if required not in meta:
            raise ValueError(f"{path} frontmatter missing required field '{required}'")
    return KBChunk(
        citation_id=str(meta["citation_id"]),
        source_url=str(meta["source_url"]),
        source_title=str(meta["source_title"]),
        retrieved_date=str(meta["retrieved_date"]),
        text=body,
        file_path=str(path),
    )


def load_chunks(kb_dir=None) -> list[KBChunk]:
    kb_dir = kb_dir or KB_DIR
    chunks = []
    for path in sorted(kb_dir.glob("*.md")):
        if path.name.upper() == "INDEX.MD":
            continue
        chunks.append(_parse_file(path))
    if not chunks:
        raise ValueError(f"No KB chunk files found in {kb_dir}")
    return chunks


def kb_version(chunks: list[KBChunk]) -> str:
    """Stable fingerprint of the corpus contents, stored alongside every
    assessment so a later rule change can be swept for affected past
    conversations (see ARCHITECTURE.md §4, audit trail)."""
    h = hashlib.sha256()
    for chunk in sorted(chunks, key=lambda c: c.citation_id):
        h.update(chunk.citation_id.encode())
        h.update(chunk.text.encode())
    return h.hexdigest()[:12]
