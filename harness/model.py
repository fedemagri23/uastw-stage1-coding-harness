"""Model clients: local Ollama for real runs, scripted replies for tests."""

import json
import urllib.error
import urllib.request

MAX_RESPONSE_BYTES = 2_000_000


class ModelError(RuntimeError):
    """The model could not be reached or returned an unusable response."""


class OllamaModel:
    """Callable client for Ollama's local chat endpoint. Returns one JSON string."""

    def __init__(self, settings):
        self.name = settings.name
        self.endpoint = settings.endpoint
        self.timeout = settings.timeout_s
        self.options = {"temperature": settings.temperature, "num_ctx": settings.num_ctx, "num_predict": 2048}

    def __call__(self, messages):
        body = {
            "model": self.name,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": self.options,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", "replace")
            raise ModelError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ModelError(f"cannot reach Ollama at {self.endpoint}: {exc}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelError("Ollama response exceeded the size limit")
        try:
            content = json.loads(raw)["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelError("Ollama response has no message.content") from exc
        if not isinstance(content, str):
            raise ModelError("message.content is not a string")
        return content


class ScriptedModel:
    """Returns pre-written replies in order; used for repeatable tests and demos.

    Items may be strings, dicts (serialised to JSON) or exceptions (raised).
    """

    name = "scripted"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(list(messages))
        try:
            reply = next(self.replies)
        except StopIteration:
            raise ModelError("scripted model has no replies left") from None
        if isinstance(reply, BaseException):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)
