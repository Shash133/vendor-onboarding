"""Tests for Agents 1–3 (Task 12, Implementation.md §4 / Day 4 tests).

Covers the document classifier, extractor, and consistency agents:
- the deterministic fallbacks return the documented output shapes,
- a forced ``AgentError`` (failing / absent client) routes through ``run()`` to
  the fallback,
- the happy path parses a model JSON response (via an injected fake client, no
  network), and
- the consistency fallback adapter honours the same contract the validation
  rules consume through ``ctx.name_match_fn`` (``default_name_match``).

No real network call is made: a fake client supplies scripted responses and a
``None`` client exercises the fallback path.
"""

from __future__ import annotations

import pytest

from agents.classifier import DocumentClassificationAgent
from agents.consistency import ConsistencyCheckingAgent
from agents.extractor import FIELD_SETS, DocumentExtractionAgent
from services.rules import default_name_match


# --- Test doubles -------------------------------------------------------------
class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, text):
        self._text = text
        self.calls = 0

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        return _FakeResponse(self._text)


class _FakeClient:
    """Returns a fixed JSON string from every generate_content call."""

    def __init__(self, text):
        self.models = _FakeModels(text)


class _FailingModels:
    def generate_content(self, *, model, contents, config):
        raise RuntimeError("simulated model failure")


class _FailingClient:
    def __init__(self):
        self.models = _FailingModels()


# ============================== CLASSIFIER (Agent 1) =========================
def test_classifier_fallback_shape():
    agent = DocumentClassificationAgent(client=None)
    out = agent.fallback(b"x", "image/png", "scan.png")
    assert out == {
        "doc_type": "OTHER",
        "confidence": 0.0,
        "reason": "classification unavailable",
        "legible": True,
    }


def test_classifier_run_falls_back_on_agent_error():
    # A failing client forces AgentError; run() must return the fallback shape.
    agent = DocumentClassificationAgent(client=_FailingClient())
    out = agent.run(b"bytes", "image/png", "scan.png")
    assert out["doc_type"] == "OTHER"
    assert out["confidence"] == 0.0
    assert out["legible"] is True


def test_classifier_run_parses_model_json():
    payload = '{"doc_type": "PAN_CARD", "confidence": 0.97, "reason": "PAN header", "legible": true}'
    agent = DocumentClassificationAgent(client=_FakeClient(payload))
    out = agent.run(b"bytes", "image/png", "pan.png")
    assert out["doc_type"] == "PAN_CARD"
    assert out["confidence"] == 0.97
    assert out["legible"] is True


# ============================== EXTRACTOR (Agent 2) ==========================
@pytest.mark.parametrize("doc_type", list(FIELD_SETS.keys()))
def test_extractor_fallback_shape_per_type(doc_type):
    agent = DocumentExtractionAgent(client=None)
    out = agent.fallback(b"x", "application/pdf", doc_type)
    expected_fields = FIELD_SETS[doc_type]
    assert set(out["fields"].keys()) == set(expected_fields)
    for name in expected_fields:
        assert out["fields"][name] == {"value": None, "confidence": 0.0, "verbatim": ""}
    assert set(out["unreadable_fields"]) == set(expected_fields)


def test_extractor_run_falls_back_on_agent_error():
    agent = DocumentExtractionAgent(client=_FailingClient())
    out = agent.run(b"bytes", "application/pdf", "PAN_CARD")
    # All fields null + every field flagged unreadable (never blocks pipeline).
    assert out["fields"]["pan"]["value"] is None
    assert out["fields"]["name"]["value"] is None
    assert set(out["unreadable_fields"]) == {"pan", "name"}


def test_extractor_run_parses_model_json():
    payload = (
        '{"fields": {"pan": {"value": "ABCDE1234F", "confidence": 0.99, "verbatim": "ABCDE1234F"},'
        ' "name": {"value": "Acme", "confidence": 0.9, "verbatim": "Acme"}},'
        ' "unreadable_fields": []}'
    )
    agent = DocumentExtractionAgent(client=_FakeClient(payload))
    out = agent.run(b"bytes", "application/pdf", "PAN_CARD")
    assert out["fields"]["pan"]["value"] == "ABCDE1234F"
    assert out["unreadable_fields"] == []


# ============================== CONSISTENCY (Agent 3) ========================
def test_consistency_fallback_shape_same_entity():
    agent = ConsistencyCheckingAgent(client=None)
    out = agent.fallback("Acme Technologies Private Limited", "Acme Technologies Pvt Ltd")
    assert set(out.keys()) == {"is_same_entity", "similarity", "normalized_a", "normalized_b", "reason"}
    assert isinstance(out["is_same_entity"], bool)
    assert 0.0 <= out["similarity"] <= 1.0


def test_consistency_fallback_empty_names():
    agent = ConsistencyCheckingAgent(client=None)
    out = agent.fallback("", "Acme")
    assert out["is_same_entity"] is False
    assert out["similarity"] == 0.0


def test_consistency_run_falls_back_on_agent_error():
    agent = ConsistencyCheckingAgent(client=_FailingClient())
    out = agent.run("Acme Tech Pvt Ltd", "Acme Technologies Private Limited")
    assert "is_same_entity" in out and "similarity" in out and "reason" in out


def test_consistency_run_parses_model_json():
    payload = (
        '{"is_same_entity": true, "similarity": 0.93, "normalized_a": "a",'
        ' "normalized_b": "b", "reason": "suffix only"}'
    )
    agent = ConsistencyCheckingAgent(client=_FakeClient(payload))
    out = agent.run("A", "B")
    assert out["is_same_entity"] is True
    assert out["similarity"] == 0.93


@pytest.mark.parametrize(
    "a,b",
    [
        ("Acme Technologies Private Limited", "Acme Technologies Pvt Ltd"),
        ("Acme Technologies Private Limited", "Zenith Foods Corporation"),
        ("Acme Corp", "Acme Corp"),
    ],
)
def test_consistency_adapter_matches_default_name_match_contract(a, b):
    """The agent's name_match_fn adapter mirrors default_name_match (fallback path)."""
    agent = ConsistencyCheckingAgent(client=None)  # no client → rapidfuzz fallback
    fn = agent.as_name_match_fn()
    adapted = fn(a, b)
    baseline = default_name_match(a, b)

    # Same contract keys as the rules consume.
    assert set(adapted.keys()) == {"is_same_entity", "similarity", "reason"}
    # Same verdict + similarity as the deterministic default matcher.
    assert adapted["is_same_entity"] == baseline["is_same_entity"]
    assert adapted["similarity"] == baseline["similarity"]
