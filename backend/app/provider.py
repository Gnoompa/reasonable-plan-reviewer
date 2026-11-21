"""Talks to a chat model over OpenRouter's OpenAI-compatible endpoint.

Standard library only, so the review command runs without extra packages and
the same code path works locally and in the container.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl:free"


class ModelError(RuntimeError):
    """The provider could not give us a usable reply."""


def model_name() -> str:
    return os.environ.get("REVIEW_MODEL", DEFAULT_MODEL)


def complete(system: str, user: str, timeout: float) -> str:
    """Return the assistant text, or raise ModelError."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ModelError("OPENROUTER_API_KEY is not set")

    body = json.dumps(
        {
            "model": model_name(),
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Plan reviewer",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise ModelError(f"provider returned HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ModelError(f"provider request failed: {error}") from error
    except ValueError as error:
        raise ModelError(f"provider reply was not JSON: {error}") from error

    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as error:
        raise ModelError(f"provider reply had no message content: {payload!r}"[:300]) from error


def parse_json(text: str) -> dict:
    """Pull one JSON object out of a reply that may be fenced or chatty."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()

    try:
        value = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ModelError("reply contained no JSON object")
        try:
            value = json.loads(cleaned[start:end + 1])
        except ValueError as error:
            raise ModelError(f"reply was not valid JSON: {error}") from error

    if not isinstance(value, dict):
        raise ModelError("reply JSON was not an object")
    return value
