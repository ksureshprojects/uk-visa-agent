from pathlib import Path

from app.kb.retrieval import KnowledgeStore

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"


def test_retrieval_ranks_relevant_chunk_first():
    store = KnowledgeStore(kb_dir=FIXTURE_KB)
    results = store.retrieve("how much money do I need to show in my bank account", top_k=2)
    assert results, "expected at least one match"
    assert results[0].chunk.citation_id == "fixture-financial-01"


def test_kb_version_is_stable_for_same_content():
    store_a = KnowledgeStore(kb_dir=FIXTURE_KB)
    store_b = KnowledgeStore(kb_dir=FIXTURE_KB)
    assert store_a.version == store_b.version


def test_citations_carry_source_metadata():
    store = KnowledgeStore(kb_dir=FIXTURE_KB)
    chunk = store.by_citation_id("fixture-financial-01")
    assert chunk is not None
    assert chunk.source_url.startswith("https://")
