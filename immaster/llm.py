"""
Pluggable local-LLM backends — the robot's brain, whatever runtime it runs on.

The brain stays on-device, but different local runtimes expose different HTTP
APIs. This module hides that behind one interface so a pilot loop can target any
of them by swapping a backend (or setting the LLM_BACKEND env var):

    from immaster.llm import make_llm
    llm = make_llm()              # picks from env; defaults to Ollama
    reply = llm.chat(messages)    # -> assistant reply text

Built-in backends:
    "ollama"        real Ollama /api/chat            (CPU or GPU, native)
    "hailo"         Hailo-Ollama /api/chat           (a model running ON the NPU)
    "openai"        /v1/chat/completions             (llama.cpp, LM Studio, vLLM)

Add your own by subclassing LLMBackend and implementing chat(); register it in
BACKENDS (or just pass the instance straight to your loop).
"""

from __future__ import annotations
import json
import os
import urllib.request
from abc import ABC, abstractmethod


class LLMBackend(ABC):

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Return the assistant's reply text for a list of chat messages."""

    def __call__(self, messages: list[dict]) -> str:
        return self.chat(messages)


def _concat_chat_content(raw: str) -> str:
    """Pull message.content out of an /api/chat reply.

    Tolerant of both a single JSON object (stream=false) and NDJSON streaming:
    it concatenates the content of every chunk it can parse.
    """
    content = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        content += (obj.get("message") or {}).get("content", "")
    return content.strip()


class OllamaBackend(LLMBackend):

    #: default host used when neither the arg nor the env var is set
    DEFAULT_HOST = "http://localhost:11434"
    HOST_ENV = "OLLAMA_HOST"

    def __init__(self, host: str | None = None, model: str | None = None, *,
                 keep_alive: int = -1, num_predict: int = 80,
                 temperature: float = 0.2, top_k: int = 20, timeout: float = 180):
        self.host = (host or os.environ.get(self.HOST_ENV, self.DEFAULT_HOST)).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
        # keep_alive=-1 keeps the model resident between calls (no reload churn);
        # a small num_predict is plenty for a one-line JSON action.
        self.keep_alive = keep_alive
        self.options = {"temperature": temperature, "num_predict": num_predict, "top_k": top_k}
        self.timeout = timeout

    def chat(self, messages: list[dict]) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self.options,
        }).encode()
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return _concat_chat_content(resp.read().decode("utf-8", "replace"))


class HailoOllamaBackend(OllamaBackend):
    DEFAULT_HOST = "http://localhost:8000"
    HOST_ENV = "HAILO_HOST"


class OpenAIBackend(LLMBackend):
    """Any OpenAI-compatible ``/v1/chat/completions`` server: llama.cpp
    (``llama-server``), LM Studio, vLLM, or Ollama's own OpenAI-compat endpoint."""

    def __init__(self, base: str | None = None, model: str | None = None, *,
                 api_key: str | None = None, temperature: float = 0.2,
                 max_tokens: int = 80, timeout: float = 120):
        self.base = (base or os.environ.get("LLM_BASE", "http://localhost:11434/v1")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "local")  # ignored by most local servers
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def chat(self, messages: list[dict]) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]


#: name -> backend class, for make_llm() and the --backend CLI flag
BACKENDS: dict[str, type[LLMBackend]] = {
    "ollama": OllamaBackend,
    "hailo": HailoOllamaBackend,
    "hailo-ollama": HailoOllamaBackend,
    "openai": OpenAIBackend,
}


def make_llm(backend: str | None = None, **kwargs) -> LLMBackend:
    """Construct a backend by name (or the LLM_BACKEND env var; default 'ollama').

        make_llm()                              # env-driven, Ollama by default
        make_llm("hailo")                       # model on the Hailo NPU
        make_llm("openai", base=..., model=..)  # llama.cpp / LM Studio / vLLM

    Any extra kwargs are forwarded to the backend's constructor.
    """
    name = (backend or os.environ.get("LLM_BACKEND", "ollama")).lower()
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise ValueError(f"unknown LLM backend {name!r}; choose from {sorted(BACKENDS)}")
    return cls(**kwargs)
