from __future__ import annotations

import json
import logging
import time

import httpx

from ia.config import LLMSettings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: LLMSettings, api_key: str):
        if not api_key:
            raise LLMError("未配置 DEEPSEEK_API_KEY")
        self.settings = settings
        self.api_key = api_key

    def _post(self, payload: dict) -> dict:
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        timeout = httpx.Timeout(
            connect=self.settings.timeout_connect,
            read=self.settings.timeout_read,
            write=10.0,
            pool=10.0,
        )
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            if attempt:
                time.sleep(1.0 if attempt == 1 else 3.0)
            try:
                resp = httpx.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=timeout,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = LLMError(f"HTTP {resp.status_code}")
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                log.warning("LLM 请求失败（第 %d 次）: %s", attempt + 1, exc)
        raise LLMError(f"LLM 请求失败: {last_error}")

    def chat_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": 1500,
        }
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"LLM 响应结构异常: {str(data)[:200]}") from exc
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM 返回非 JSON: {content[:120]}") from exc
        if not isinstance(parsed, dict):
            raise LLMError("LLM 返回的 JSON 不是对象")
        return parsed
