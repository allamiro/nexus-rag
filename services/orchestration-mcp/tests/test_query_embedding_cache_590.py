"""Issue #590: the query-side embedding call is cached, bounded, and hash-keyed.

Measured against a live stack before this existed: `embed` was 7.08s of a 9.22s
mean query total across 311 queries, Qdrant itself 0.03s, and nothing cached the
call -- an identical query paid the full embed every time. NFR-4 targets p95 <= 2.5s
on that same span; 6 of 311 queries met it.

The tests that matter here are not "does it cache" but the three properties that
make caching safe to do at all:

  * the key carries the model and prefix, so a model change cannot serve vectors
    produced by the previous one (the failure the #525 config fingerprint guards
    against on the eval side);
  * the key is a digest, not the query text, because the audit log deliberately
    does not retain query text (#125) and a plaintext-keyed cache would quietly
    reintroduce that retention in process memory;
  * a failed embedding is not cached, or one upstream blip would be served for the
    lifetime of the entry.
"""

from __future__ import annotations

import asyncio

import pytest

from app import rag_search


@pytest.fixture(autouse=True)
def _clear_cache():
    rag_search.query_embedding_cache_clear()
    yield
    rag_search.query_embedding_cache_clear()


class _Recorder:
    """Stands in for the Ollama round trip, counting calls."""

    def __init__(self, vector: list[float] | None = None, fail_times: int = 0) -> None:
        self.calls: list[str] = []
        self.vector = vector or [0.1, 0.2, 0.3]
        self.fail_times = fail_times

    async def __call__(self, _client, _url, _model, text):
        self.calls.append(text)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("embedding endpoint unreachable")
        return list(self.vector)


def _embed(query: str) -> list[float]:
    return asyncio.run(rag_search._embed_query(query))


def test_an_identical_query_is_embedded_once(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rag_search, "request_embedding", recorder)

    first = _embed("how often must passwords be rotated")
    second = _embed("how often must passwords be rotated")

    assert first == second
    assert len(recorder.calls) == 1, f"embedded {len(recorder.calls)} times, expected 1"


def test_a_different_query_is_embedded_again(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rag_search, "request_embedding", recorder)

    _embed("password rotation")
    _embed("vpn access request")

    assert len(recorder.calls) == 2


def test_the_query_text_is_never_used_as_a_key(monkeypatch):
    """#125: query text is deliberately not retained anywhere.

    A cache keyed on plaintext would hold every query string in process memory for
    the cache's lifetime -- a retention property arrived at by accident rather than
    decided. The key must be a digest.
    """
    recorder = _Recorder()
    monkeypatch.setattr(rag_search, "request_embedding", recorder)
    secret = "which programs use the classified component codenamed BLUEJAY"
    _embed(secret)

    keys = list(rag_search._embedding_cache.keys())
    assert keys, "nothing cached, so this test proves nothing"
    for key in keys:
        assert secret not in key
        assert "BLUEJAY" not in key
        assert len(key) == 64 and all(c in "0123456789abcdef" for c in key), (
            f"key {key!r} is not a sha256 digest"
        )


def test_the_key_includes_the_model_so_a_model_change_cannot_be_served_stale(monkeypatch):
    recorder = _Recorder(vector=[1.0, 1.0])
    monkeypatch.setattr(rag_search, "request_embedding", recorder)
    monkeypatch.setattr(rag_search, "EMBEDDING_MODEL", "model-a")
    first = _embed("same question")

    recorder.vector = [2.0, 2.0]
    monkeypatch.setattr(rag_search, "EMBEDDING_MODEL", "model-b")
    second = _embed("same question")

    assert first == [1.0, 1.0]
    assert second == [2.0, 2.0], "a model change served the previous model's vector"
    assert len(recorder.calls) == 2


def test_the_key_includes_the_prefix(monkeypatch):
    """The query prefix is part of what was embedded (#392), so it is part of the key."""
    recorder = _Recorder()
    monkeypatch.setattr(rag_search, "request_embedding", recorder)
    monkeypatch.setattr(rag_search, "query_prefix", lambda _model: "search_query: ")
    _embed("q")
    monkeypatch.setattr(rag_search, "query_prefix", lambda _model: "different: ")
    _embed("q")
    assert len(recorder.calls) == 2
    assert recorder.calls[0] != recorder.calls[1]


def test_a_failed_embedding_is_not_cached(monkeypatch):
    """One upstream blip must not be served for the entry's lifetime."""
    recorder = _Recorder(fail_times=1)
    monkeypatch.setattr(rag_search, "request_embedding", recorder)

    with pytest.raises(RuntimeError):
        _embed("transient failure")
    assert rag_search._embedding_cache == {}

    assert _embed("transient failure") == [0.1, 0.2, 0.3]
    assert len(recorder.calls) == 2


def test_the_cache_is_bounded_and_evicts_least_recently_used(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rag_search, "request_embedding", recorder)
    monkeypatch.setattr(rag_search, "QUERY_EMBEDDING_CACHE_SIZE", 3)

    for i in range(3):
        _embed(f"query-{i}")
    assert len(rag_search._embedding_cache) == 3

    _embed("query-0")  # refresh the oldest so it is no longer LRU
    _embed("query-3")  # evicts query-1, not query-0
    assert len(rag_search._embedding_cache) == 3

    calls_before = len(recorder.calls)
    _embed("query-0")
    assert len(recorder.calls) == calls_before, "query-0 should still be cached"
    _embed("query-1")
    assert len(recorder.calls) == calls_before + 1, "query-1 should have been evicted"


def test_setting_the_size_to_zero_disables_caching(monkeypatch):
    """An operator must be able to turn it off without a code change."""
    recorder = _Recorder()
    monkeypatch.setattr(rag_search, "request_embedding", recorder)
    monkeypatch.setattr(rag_search, "QUERY_EMBEDDING_CACHE_SIZE", 0)

    _embed("same")
    _embed("same")

    assert len(recorder.calls) == 2
    assert rag_search._embedding_cache == {}


def test_a_returned_vector_cannot_be_mutated_into_the_cache(monkeypatch):
    """Callers get a copy; otherwise one caller's in-place edit poisons every later
    hit on that query."""
    recorder = _Recorder(vector=[1.0, 2.0])
    monkeypatch.setattr(rag_search, "request_embedding", recorder)

    _embed("mutate me")  # miss: stores a copy
    from_hit = _embed("mutate me")  # hit: must also be a copy
    from_hit.append(99.0)  # a caller mutating what it was handed
    again = _embed("mutate me")  # hit again

    assert again == [1.0, 2.0], (
        "mutating a vector obtained from a cache hit corrupted the cached entry"
    )
    # First version of this test mutated the *miss* result, which is a distinct list
    # regardless -- so it passed even with the copy-on-read removed and proved only
    # copy-on-store.


def test_hits_and_misses_are_counted(monkeypatch):
    """A falling hit ratio is the signal that embed latency is about to rise."""
    recorder = _Recorder()
    monkeypatch.setattr(rag_search, "request_embedding", recorder)

    def total(outcome: str) -> float:
        from prometheus_client import REGISTRY

        value = REGISTRY.get_sample_value(
            "nexus_rag_query_embedding_cache_total", {"outcome": outcome}
        )
        return value or 0.0

    misses_before, hits_before = total("miss"), total("hit")
    _embed("counted")
    _embed("counted")

    assert total("miss") == misses_before + 1
    assert total("hit") == hits_before + 1
