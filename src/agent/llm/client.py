from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
import socket
from typing import Optional
from urllib import request, error

from agent.errors import ToolError


@dataclass
class LLMClient:
    """
    Minimal abstraction over LLM providers used in this repo.

    Supported providers:
      - "google" (Gemini via google-genai)
      - "deepseek" (OpenAI-compatible chat completions API)
    """

    provider: str
    model: str
    api_key: Optional[str] = None

    # Internal singletons created lazily
    _google_client: object | None = None

    def _get_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        # Environment fallbacks by provider
        if self.is_google:
            return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if self.is_deepseek:
            return os.getenv("DEEPSEEK_API_KEY")
        return None

    @property
    def is_google(self) -> bool:
        p = (self.provider or "").lower()
        m = (self.model or "").lower()
        return p.startswith("google") or p.startswith("gemini") or m.startswith("gemini")

    @property
    def is_deepseek(self) -> bool:
        p = (self.provider or "").lower()
        m = (self.model or "").lower()
        return p.startswith("deepseek") or m.startswith("deepseek")

    # --- Public API ---------------------------------------------------------
    def generate(self, *, system_instruction: Optional[str], user_text: str, max_output_tokens: int) -> str:
        if self.is_google:
            return self._generate_google(system_instruction=system_instruction, user_text=user_text, max_output_tokens=max_output_tokens)
        if self.is_deepseek:
            return self._generate_deepseek(system_instruction=system_instruction, user_text=user_text, max_output_tokens=max_output_tokens)
        raise ToolError(f"Unsupported provider: {self.provider}", tool_name="llm")

    # --- Provider impls -----------------------------------------------------
    def _generate_google(self, *, system_instruction: Optional[str], user_text: str, max_output_tokens: int) -> str:
        try:
            # Lazy import to avoid hard dependency when not needed
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ToolError("google-genai SDK not installed", tool_name="llm_google") from e

        if self._google_client is None:
            key = self._get_key()
            self._google_client = genai.Client(api_key=key) if key else genai.Client()

        client = self._google_client
        assert client is not None

        cfg = types.GenerateContentConfig(
            system_instruction=system_instruction or "",
            max_output_tokens=max_output_tokens,
        )
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=user_text)],
            )
        ]

        try:
            response = client.models.generate_content(model=self.model, config=cfg, contents=contents)
        except Exception as e:  # pragma: no cover - network path
            raise ToolError(f"Google GenAI request failed: {e}", tool_name="llm_google")

        return getattr(response, "text", None) or ""

    def _generate_deepseek(self, *, system_instruction: Optional[str], user_text: str, max_output_tokens: int) -> str:
        key = self._get_key()
        if not key:
            raise ToolError("Missing DEEPSEEK_API_KEY", tool_name="llm_deepseek")

        url = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com") + "/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        messages = []
        if (system_instruction or "").strip():
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max(1, int(max_output_tokens or 1024)),
            # You may add temperature/top_p here if desired
            "stream": False,
        }

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")

        # Simple retry for transient errors
        retries = int(os.getenv("DEEPSEEK_RETRIES", "2") or 2)
        backoff = float(os.getenv("DEEPSEEK_BACKOFF", "1.5") or 1.5)
        # Reasoning models typically take longer; increase default timeout
        _default_timeout = 120.0 if ("reason" in (self.model or "").lower() or "r1" in (self.model or "").lower()) else 60.0
        timeout = float(os.getenv("DEEPSEEK_TIMEOUT", str(_default_timeout)) or _default_timeout)

        for attempt in range(retries + 1):
            try:
                with request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    obj = json.loads(raw)
                    choices = obj.get("choices") or []
                    if not choices:
                        raise ToolError("DeepSeek: empty choices", tool_name="llm_deepseek")
                    content = choices[0].get("message", {}).get("content", "")
                    return content or ""
            except error.HTTPError as e:  # pragma: no cover - network path
                try:
                    body = e.read().decode("utf-8")
                except Exception:
                    body = str(e)
                if e.code in (429, 503) and attempt < retries:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                raise ToolError(f"DeepSeek HTTP {e.code}: {body}", tool_name="llm_deepseek")
            except error.URLError as e:  # pragma: no cover - network path
                if attempt < retries:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                raise ToolError(f"DeepSeek network error: {e}", tool_name="llm_deepseek")
            except socket.timeout as e:  # pragma: no cover - network path
                if attempt < retries:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                raise ToolError(f"DeepSeek timeout: {e}", tool_name="llm_deepseek")
            except Exception as e:  # pragma: no cover - network path
                if attempt < retries:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                raise ToolError(f"DeepSeek request failed: {e}", tool_name="llm_deepseek")

        # Should not reach here
        raise ToolError("DeepSeek: retries exhausted", tool_name="llm_deepseek")
