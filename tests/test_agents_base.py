"""Tests for the Gemini agent base class (Task 4, Implementation.md §4).

These exercise the generic machinery with a fake client so no network call is
made: prompt loading/caching, JSON parsing, retry-then-AgentError, the run()
fallback path, and the lazily-imported, failure-tolerant audit hook.
"""

import json

import pytest

from agents.base import AgentError, GeminiAgent, make_client


# --- Test doubles -------------------------------------------------------------
class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    """Stand-in for client.models with a scripted sequence of outcomes."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)


class _FakeClient:
    def __init__(self, outcomes):
        self.models = _FakeModels(outcomes)


class _EchoAgent(GeminiAgent):
    name = "echo"
    response_schema = {"type": "object"}

    def _build_parts(self, *args, **kwargs):
        return [self._load_prompt(), *args]

    def fallback(self, *args, **kwargs):
        return {"fallback": True}


@pytest.fixture()
def prompt_file(tmp_path):
    p = tmp_path / "echo.txt"
    p.write_text("PROMPT-TEXT", encoding="utf-8")
    return str(p)


# --- make_client --------------------------------------------------------------
def test_make_client_raises_without_api_key(monkeypatch):
    import agents.base as base
    monkeypatch.setattr(base, "GEMINI_API_KEY", "")
    with pytest.raises(AgentError):
        make_client()


# --- prompt loading -----------------------------------------------------------
def test_load_prompt_reads_and_caches(prompt_file):
    agent = _EchoAgent(_FakeClient([]), prompt_file)
    assert agent._load_prompt() == "PROMPT-TEXT"
    # Mutate the cache to prove the second read is served from cache.
    agent._prompt_cache = "CACHED"
    assert agent._load_prompt() == "CACHED"


# --- _call success / parsing --------------------------------------------------
def test_call_parses_json_object(prompt_file):
    client = _FakeClient([json.dumps({"doc_type": "PAN_CARD"})])
    agent = _EchoAgent(client, prompt_file)
    result = agent._call(["x"])
    assert result == {"doc_type": "PAN_CARD"}
    assert client.models.calls == 1


def test_call_retries_once_then_succeeds(prompt_file):
    client = _FakeClient([RuntimeError("boom"), json.dumps({"ok": 1})])
    agent = _EchoAgent(client, prompt_file)
    assert agent._call(["x"]) == {"ok": 1}
    assert client.models.calls == 2  # one failure + one retry


def test_call_raises_agent_error_after_retry(prompt_file):
    client = _FakeClient([RuntimeError("boom1"), RuntimeError("boom2")])
    agent = _EchoAgent(client, prompt_file)
    with pytest.raises(AgentError):
        agent._call(["x"])
    assert client.models.calls == 2


def test_call_invalid_json_raises_agent_error(prompt_file):
    client = _FakeClient(["not-json", "still-not-json"])
    agent = _EchoAgent(client, prompt_file)
    with pytest.raises(AgentError):
        agent._call(["x"])


# --- run() fallback + audit ---------------------------------------------------
def test_run_returns_parsed_response(prompt_file):
    client = _FakeClient([json.dumps({"value": 42})])
    agent = _EchoAgent(client, prompt_file)
    assert agent.run("input") == {"value": 42}


def test_run_falls_back_on_agent_error(prompt_file):
    client = _FakeClient([RuntimeError("a"), RuntimeError("b")])
    agent = _EchoAgent(client, prompt_file)
    assert agent.run("input") == {"fallback": True}


def test_run_does_not_crash_when_audit_service_missing(prompt_file):
    # services.audit_service does not exist yet (Task 6); run() must still work.
    client = _FakeClient([json.dumps({"ok": True})])
    agent = _EchoAgent(client, prompt_file)
    assert agent.run("input", submission_id="sub-123") == {"ok": True}


def test_base_fallback_not_implemented(prompt_file):
    agent = GeminiAgent(_FakeClient([]), prompt_file)
    with pytest.raises(NotImplementedError):
        agent.fallback()


def test_base_build_parts_not_implemented(prompt_file):
    agent = GeminiAgent(_FakeClient([]), prompt_file)
    with pytest.raises(NotImplementedError):
        agent._build_parts()
