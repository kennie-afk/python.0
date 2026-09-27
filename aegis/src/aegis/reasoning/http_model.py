from __future__ import annotations

import os

import httpx

from aegis.reasoning.provider import Completion, Prompt, ReasoningError


class HttpLanguageModel:
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ReasoningError("an API key is required to reach a hosted model")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @classmethod
    def from_environment(cls) -> HttpLanguageModel:
        api_key = os.environ.get("AEGIS_MODEL_API_KEY", "")
        if not api_key:
            raise ReasoningError(
                "AEGIS_MODEL_API_KEY is not set; the platform runs on the deterministic "
                "model unless a hosted one is configured"
            )
        return cls(
            model=os.environ.get("AEGIS_MODEL_NAME", "gpt-4o-mini"),
            base_url=os.environ.get("AEGIS_MODEL_BASE_URL", "https://api.openai.com/v1"),
            api_key=api_key,
        )

    @property
    def name(self) -> str:
        return self._model

    def complete(self, prompt: Prompt) -> Completion:
        body = {
            "model": self._model,
            "temperature": prompt.temperature,
            "max_tokens": prompt.max_tokens,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        if prompt.schema_hint:
            body["response_format"] = {"type": "json_object"}

        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as error:
            raise ReasoningError(f"model request failed: {error}") from error

        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ReasoningError(f"unexpected response shape from {self._model}") from error

        usage = payload.get("usage") or {}
        return Completion(
            text=str(text),
            model=self._model,
            prompt_fingerprint=prompt.fingerprint(),
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
            },
        )

    def close(self) -> None:
        self._client.close()
