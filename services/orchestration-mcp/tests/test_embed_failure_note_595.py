"""Issue #595: an embedding failure must not be reported as an empty corpus.

Found under load, not by reading the code. 201 warnings said "vector backend
qdrant unavailable: ReadTimeout" while Qdrant sat at 1.1% CPU and 717MB --
healthy, and never called. Both dependencies were caught by one
`except (VectorStoreUnavailable, httpx.HTTPError)`, and every backend wraps its
own failures in VectorStoreUnavailable (qdrant_backend.py:119/154,
milvus_store.py:326/360), so a bare httpx error reaching that handler could only
have come from the embedding call -- and was logged under the other dependency's
name.

The note mattered more than the log. It said:

    "the search index is not queryable; it's created lazily on first ingestion,
     so this is expected if no document has been submitted yet"

That string is returned through the MCP tool into a model's context, so on an
embedding timeout against a populated, curated corpus the model was handed a
confident false premise -- no documents exist, and this is expected -- which is
what it then tells the user. Fail-closed (zero results) is correct under FR-26;
explaining the closure with the wrong reason is not.

These tests pin the distinction, not the wording: a timeout must not assert an
empty corpus, must name the dependency that actually failed, and must stay
auditable and empty.
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest
from qdrant_client.models import SparseVector

from app import metrics, rag_search
from common.vector_store import VectorStoreUnavailable


class _Claims:
    sub = "u1"
    preferred_username = "bob-query"
    clearance = "SECRET"
    groups = ("analysts",)
    org = "USAREUR-AF"
    can_query = True
    releasability = ("FVEY",)


def _stub(monkeypatch, *, embed_error=None, store_error=None, audits: list) -> None:
    @contextmanager
    def _session():
        yield object()

    class _Store:
        def hybrid_query(self, **_kwargs):
            if store_error is not None:
                raise store_error
            return []

        def access_filter_summary(self, _claims, _allowed):
            return {"backend": "fake"}

        def stored_embedding_model(self):
            return None

    async def _embed(_q):
        if embed_error is not None:
            raise embed_error
        return [0.0]

    monkeypatch.setattr(rag_search, "parse_claims", lambda _t: _Claims())
    monkeypatch.setattr(rag_search, "get_session", lambda: iter([_session()]))
    monkeypatch.setattr(rag_search, "allowed_classifications", lambda _s, _c: ["SECRET"])
    monkeypatch.setattr(rag_search, "_embed_query", _embed)
    monkeypatch.setattr(
        rag_search, "embed_sparse", lambda _t: [SparseVector(indices=[0], values=[1.0])]
    )
    monkeypatch.setattr(rag_search, "get_store", _Store)
    monkeypatch.setattr(
        rag_search, "_audit", lambda claims, action, detail: audits.append((action, detail))
    )


def _count(outcome: str) -> float:
    return metrics.queries_total.labels(outcome=outcome)._value.get()


async def _search(monkeypatch, **kwargs) -> dict:
    audits: list = []
    _stub(monkeypatch, audits=audits, **kwargs)
    result = await rag_search.run_rag_search("token", "what is the escalation timeline", top_k=5)
    result["_audits"] = audits
    return result


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection refused"),
    ],
    ids=["timeout", "connect-refused"],
)
async def test_an_embedding_failure_does_not_claim_the_corpus_is_empty(monkeypatch, error):
    """The bug, stated as the thing a user would be told."""
    result = await _search(monkeypatch, embed_error=error)
    note = result["note"].lower()

    assert "no document has been submitted" not in note, (
        f"an embedding failure told the caller the corpus is empty: {result['note']!r}"
    )
    # "created lazily on first ingestion" is a claim about ingestion state. It is
    # not knowable from a failure in the embedding call and must not be asserted.
    assert "expected if no document" not in note
    assert "temporary" in note or "retry" in note, (
        f"nothing told the caller this is transient: {result['note']!r}"
    )


async def test_an_embedding_failure_names_the_embedding_service_not_the_vector_backend(
    monkeypatch, caplog
):
    """The 201-warnings half: an operator paged by this must not go debug Qdrant."""
    with caplog.at_level("WARNING"):
        await _search(monkeypatch, embed_error=httpx.ReadTimeout("timed out"))

    logged = " ".join(record.getMessage().lower() for record in caplog.records)
    assert "embedding" in logged, f"the failing dependency was not named: {logged!r}"
    assert "vector backend" not in logged, (
        f"an embedding failure was logged as a vector-backend outage: {logged!r}"
    )


async def test_an_embedding_failure_is_a_distinct_metric_outcome(monkeypatch):
    """Otherwise the two failures are indistinguishable on a dashboard, which is
    how this went unnoticed: outcome="unavailable" was already rising."""
    before_embed = _count("embedding_unavailable")
    before_store = _count("unavailable")

    await _search(monkeypatch, embed_error=httpx.ReadTimeout("timed out"))

    assert _count("embedding_unavailable") == before_embed + 1
    assert _count("unavailable") == before_store, (
        "an embedding failure incremented the vector-store outcome"
    )


async def test_an_embedding_failure_still_returns_nothing_and_is_still_audited(monkeypatch):
    """FR-26 fail-closed and FR-31 coverage are the parts that were already right."""
    result = await _search(monkeypatch, embed_error=httpx.ReadTimeout("timed out"))

    assert result["results"] == []
    actions = [action for action, _detail in result["_audits"]]
    assert actions == ["query"], f"the failed attempt was not audited exactly once: {actions}"
    assert result["_audits"][0][1]["result_count"] == 0


async def test_a_real_vector_store_failure_still_reports_the_vector_store(monkeypatch):
    """The other side of the split -- the existing behaviour must survive it."""
    before = _count("unavailable")
    result = await _search(monkeypatch, store_error=VectorStoreUnavailable("connection refused"))

    assert result["results"] == []
    assert _count("unavailable") == before + 1
    note = result["note"].lower()
    assert "index" in note
    # Lazy creation is still offered for the case where it genuinely applies --
    # but as a possibility, not as the reason.
    assert "lazily" in note


async def test_a_vector_store_failure_is_not_reported_as_an_embedding_failure(monkeypatch):
    before = _count("embedding_unavailable")
    await _search(monkeypatch, store_error=VectorStoreUnavailable("connection refused"))
    assert _count("embedding_unavailable") == before
