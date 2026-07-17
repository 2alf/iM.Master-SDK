"""Unit tests for the pluggable LLM backend layer — no network calls."""

import pytest

from immaster import llm


def test_default_backend_is_ollama():
    assert isinstance(llm.make_llm(), llm.OllamaBackend)


def test_hailo_backend_selected_by_name():
    b = llm.make_llm("hailo")
    assert isinstance(b, llm.HailoOllamaBackend)
    assert b.host.endswith(":8000")          # NPU server default port
    assert isinstance(b, llm.OllamaBackend)  # shares the /api/chat wire format


def test_openai_backend_and_kwargs_forwarded():
    b = llm.make_llm("openai", base="http://host:1234/v1", model="my-model")
    assert isinstance(b, llm.OpenAIBackend)
    assert b.base == "http://host:1234/v1"
    assert b.model == "my-model"


def test_backend_from_env(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "hailo-ollama")
    assert isinstance(llm.make_llm(), llm.HailoOllamaBackend)


def test_unknown_backend_raises():
    with pytest.raises(ValueError):
        llm.make_llm("gpt-9000")


def test_all_backends_are_llm_backends():
    for cls in set(llm.BACKENDS.values()):
        assert issubclass(cls, llm.LLMBackend)


def test_content_parser_single_json():
    raw = '{"message": {"content": "forward now"}}'
    assert llm._concat_chat_content(raw) == "forward now"


def test_content_parser_ndjson_stream():
    raw = '{"message":{"content":"for"}}\n{"message":{"content":"ward"}}\n'
    assert llm._concat_chat_content(raw) == "forward"


def test_content_parser_ignores_garbage_lines():
    raw = 'not json\n{"message":{"content":"ok"}}\n'
    assert llm._concat_chat_content(raw) == "ok"
