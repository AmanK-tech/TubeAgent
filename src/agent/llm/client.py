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
    Minimal abstraction for DeepSeek chat completions.

    Only DeepSeek is supported in this build.
    """

    provider: str  # kept for compatibility; ignored except for error messages
    model: str
    api_key: Optional[str] = None

    def _get_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        return os.getenv("DEEPSEEK_API_KEY")

    # --- Public API ---------------------------------------------------------
    def generate(self, *, system_instruction: Optional[str], user_text: str, max_output_tokens: int) -> str:
        # Always use DeepSeek; other providers are not supported
        return self._generate_deepseek(system_instruction=system_instruction, user_text=user_text, max_output_tokens=max_output_tokens)

    # --- Provider impl ------------------------------------------------------
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

    # --- Raw chat for function calling --------------------------------------
    def chat_raw(
        self,
        *,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[object] = None,
        max_output_tokens: int = 1024,
    ) -> dict:
        """
        Low-level chat call returning the parsed JSON response (supports tools).

        - messages: list of {role, content, ...}
        - tools: list of tool/function schemas as returned by get_tools()
        - tool_choice: None, "auto", "required", or a provider-specific object
        """
        key = self._get_key()
        if not key:
            raise ToolError("Missing DEEPSEEK_API_KEY", tool_name="llm_deepseek")

        url = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com") + "/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max(1, int(max_output_tokens or 1024)),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")

        retries = int(os.getenv("DEEPSEEK_RETRIES", "2") or 2)
        backoff = float(os.getenv("DEEPSEEK_BACKOFF", "1.5") or 1.5)
        _default_timeout = 120.0 if ("reason" in (self.model or "").lower() or "r1" in (self.model or "").lower()) else 60.0
        timeout = float(os.getenv("DEEPSEEK_TIMEOUT", str(_default_timeout)) or _default_timeout)

        for attempt in range(retries + 1):
            try:
                with request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    obj = json.loads(raw)
                    return obj
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
