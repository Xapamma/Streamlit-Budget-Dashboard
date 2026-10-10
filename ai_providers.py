"""Small provider layer for user-supplied AI keys (Gemini, OpenRouter, Ollama).

Keys are passed in explicitly per call and are never stored, logged, or included
in error messages by this module.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

REQUEST_TIMEOUT_SECONDS = 60
LOCAL_OLLAMA_URL = "http://localhost:11434"
CLOUD_OLLAMA_URL = "https://ollama.com"
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._:/\-]{1,100}$")


class AIError(RuntimeError):
    """A safe, user-presentable AI failure. Never contains credentials."""


@dataclass(frozen=True)
class ProviderInfo:
    label: str
    needs_key: bool
    default_model: str
    suggested_models: tuple[str, ...]
    key_url: str
    docs_url: str
    privacy_url: str
    privacy_notice: str
    requires_acknowledgement: bool


PROVIDERS: dict[str, ProviderInfo] = {
    "gemini": ProviderInfo(
        label="Google Gemini",
        needs_key=True,
        default_model="gemini-2.5-flash",
        suggested_models=("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"),
        key_url="https://aistudio.google.com/apikey",
        docs_url="https://ai.google.dev/gemini-api/docs/pricing",
        privacy_url="https://ai.google.dev/gemini-api/terms",
        privacy_notice=(
            "On Google's unpaid Gemini API tier, submitted content and responses may be used to "
            "improve Google products and may be reviewed by humans. Paid (billing-enabled) usage "
            "has different terms. Do not send sensitive or personal information."
        ),
        requires_acknowledgement=True,
    ),
    "openrouter": ProviderInfo(
        label="OpenRouter",
        needs_key=True,
        default_model="openrouter/free",
        suggested_models=("openrouter/free", "google/gemini-2.5-flash", "openai/gpt-4o-mini"),
        key_url="https://openrouter.ai/keys",
        docs_url="https://openrouter.ai/models",
        privacy_url="https://openrouter.ai/privacy",
        privacy_notice=(
            "OpenRouter forwards your prompt to the underlying model provider, whose retention "
            "and training terms differ and may allow training (especially on free models). "
            "Policies could not be verified per model. Do not send sensitive information."
        ),
        requires_acknowledgement=True,
    ),
    "ollama_cloud": ProviderInfo(
        label="Ollama Cloud",
        needs_key=True,
        default_model="gpt-oss:120b",
        suggested_models=("gpt-oss:120b", "gpt-oss:20b"),
        key_url="https://ollama.com/settings/keys",
        docs_url="https://docs.ollama.com/cloud",
        privacy_url="https://ollama.com/privacy",
        privacy_notice=(
            "Ollama's published policy says cloud prompts and responses are processed to serve the "
            "request and are not used for training. Confirm this in the policy before sending "
            "anything sensitive."
        ),
        requires_acknowledgement=True,
    ),
    "ollama_local": ProviderInfo(
        label="Ollama (local, this computer only)",
        needs_key=False,
        default_model="gemma3:4b",
        suggested_models=("gemma3:4b", "qwen3:8b"),
        key_url="https://ollama.com/download",
        docs_url="https://docs.ollama.com",
        privacy_url="https://ollama.com/privacy",
        privacy_notice=(
            "Requests go to Ollama on the same computer as this app. This only works when you run "
            "the app locally; a hosted app (e.g. Streamlit Community Cloud) cannot reach your "
            "computer's Ollama."
        ),
        requires_acknowledgement=False,
    ),
}


@dataclass(frozen=True)
class AISettings:
    provider: str
    model: str
    api_key: str = field(default="", repr=False)


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "[redacted]") if api_key else text


def _post_json(url: str, headers: Mapping[str, str], body: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        code = error.code
        if code in (401, 403):
            message = "The provider rejected the API key. Check that it is valid and has access."
        elif code == 404:
            message = "The model was not found. Check the model name for this provider."
        elif code == 429:
            message = "Rate limit or quota reached. Wait and retry, or check your plan and usage."
        elif code in (402,):
            message = "The provider reports a billing or credit problem on your account."
        elif code >= 500:
            message = "The provider is having problems. Try again later."
        else:
            message = (
                f"The provider rejected the request (HTTP {code}). Check the API key, model "
                "name, and prompt size."
            )
        raise AIError(message) from None
    except urllib.error.URLError:
        raise AIError("Could not reach the AI provider. Check your connection or that Ollama is running.") from None
    except TimeoutError:
        raise AIError("The AI provider timed out. Try again.") from None

    try:
        payload = json.loads(raw)
    except ValueError:
        raise AIError("The provider returned an unreadable response.") from None
    if not isinstance(payload, dict):
        raise AIError("The provider returned an unexpected response.")
    return payload


def _gemini(settings: AISettings, messages: Sequence[Mapping[str, str]], json_mode: bool) -> str:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
        if m["role"] != "system"
    ]
    body: dict = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    model = urllib.parse.quote(settings.model.removeprefix("models/"), safe="")
    payload = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": settings.api_key},
        body,
        settings.api_key,
    )
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIError("Gemini returned no answer (the content may have been blocked).") from None


def _openrouter(settings: AISettings, messages: Sequence[Mapping[str, str]], json_mode: bool) -> str:
    payload = _post_json(
        "https://openrouter.ai/api/v1/chat/completions",
        {"Authorization": f"Bearer {settings.api_key}"},
        {"model": settings.model, "messages": [dict(m) for m in messages]},
        settings.api_key,
    )
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError("OpenRouter returned no answer. The model may be unavailable.") from None


def _ollama(base_url: str) -> Callable[[AISettings, Sequence[Mapping[str, str]], bool], str]:
    def send(settings: AISettings, messages: Sequence[Mapping[str, str]], json_mode: bool) -> str:
        body: dict = {
            "model": settings.model,
            "messages": [dict(m) for m in messages],
            "stream": False,
        }
        if json_mode:
            body["format"] = "json"
        headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
        payload = _post_json(f"{base_url}/api/chat", headers, body, settings.api_key)
        try:
            return payload["message"]["content"] or ""
        except (KeyError, TypeError):
            raise AIError("Ollama returned no answer.") from None

    return send


_DISPATCH: dict[str, Callable[[AISettings, Sequence[Mapping[str, str]], bool], str]] = {
    "gemini": _gemini,
    "openrouter": _openrouter,
    "ollama_cloud": _ollama(CLOUD_OLLAMA_URL),
    "ollama_local": _ollama(LOCAL_OLLAMA_URL),
}


def validate_settings(settings: AISettings | None) -> AISettings:
    if settings is None:
        raise AIError(
            "Finish the sidebar AI settings first (provider, API key, model, and privacy confirmation)."
        )
    info = PROVIDERS.get(settings.provider)
    if info is None:
        raise AIError("Unknown AI provider.")
    if info.needs_key and not settings.api_key.strip():
        raise AIError(f"Enter your {info.label} API key in the sidebar AI settings first.")
    if not MODEL_PATTERN.match(settings.model or ""):
        raise AIError("Enter a valid model name in the sidebar AI settings.")
    return settings


def chat(
    settings: AISettings | None,
    messages: Sequence[Mapping[str, str]],
    *,
    json_mode: bool = False,
) -> str:
    """Send messages to the selected provider only; returns the reply text."""
    settings = validate_settings(settings)
    try:
        reply = _DISPATCH[settings.provider](settings, messages, json_mode)
    except AIError as error:
        raise AIError(_redact(str(error), settings.api_key)) from None
    if not reply.strip():
        raise AIError("The model returned an empty response. Try again.")
    return reply


def chat_json(settings: AISettings | None, prompt: str) -> dict:
    """Ask for a JSON object; tolerates markdown code fences around it."""
    reply = chat(settings, [{"role": "user", "content": prompt}], json_mode=True).strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", reply, re.DOTALL)
    if fenced:
        reply = fenced.group(1)
    try:
        payload = json.loads(reply)
    except json.JSONDecodeError:
        raise ValueError("The AI returned invalid JSON.") from None
    if not isinstance(payload, dict):
        raise ValueError("The AI must return a JSON object.")
    return payload
