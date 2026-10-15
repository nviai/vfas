"""OpenAI Chat Completions client (httpx).

Works with any OpenAI-compatible provider. `stream()` sends
``stream=true``, reads the SSE ``data: {...}`` lines and yields:

- ``{"type": "text", "text": "..."}`` – each content chunk;
- ``{"type": "done", "message": {...}, "finish_reason": ...,
  "usage": {...} | None}``.

``message`` is the complete assistant message in OpenAI format
(content plus tool_calls merged by index).

Tests: ``set_transport(httpx.MockTransport(handler))`` replaces the real
network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx

TIMEOUT = httpx.Timeout(120.0, connect=10.0)
# Per-call limits on total time and response size (guards against
# servers that trickle data or send huge responses).
MAX_SECONDS = 180.0
MAX_BYTES = 2_000_000
_transport: httpx.BaseTransport | None = None


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Replace the network transport with a fake one (tests only)."""
    global _transport
    _transport = transport


def test_transport_active() -> bool:
    return _transport is not None


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _error_message(resp: httpx.Response) -> str:
    """Extract the standard OpenAI error message.

    Only ``{"error": {"message"}}`` is used; the raw server response is
    never echoed back.
    """
    try:
        body = json.loads(resp.content[:20000] or b"{}")
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:200]
        if isinstance(err, str):
            return err[:200]
    except (ValueError, json.JSONDecodeError):
        pass
    return resp.reason_phrase or "lỗi không xác định"


def _limited_body(resp: httpx.Response, started: float) -> bytes:
    chunks, size = [], 0
    for chunk in resp.iter_bytes():
        size += len(chunk)
        if size > MAX_BYTES:
            raise LLMError("Phản hồi của nhà cung cấp AI quá lớn")
        if time.monotonic() - started > MAX_SECONDS:
            raise LLMError("Nhà cung cấp AI phản hồi quá lâu (timeout)")
        chunks.append(chunk)
    return b"".join(chunks)


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.transport = transport or _transport

    @staticmethod
    def _read_limited(resp: httpx.Response, started: float) -> None:
        """Read the response body with limits and attach it to ``resp``.

        ``resp.content`` can be used afterwards.
        """
        resp._content = _limited_body(resp, started)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _payload(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int | None,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        # Left out when None: some models reject the parameter.
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens
        return payload

    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[dict]:
        payload = self._payload(messages, tools, max_tokens)
        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish = None
        usage = None
        if self.transport is None:
            # Re-check the address right before the call (narrows the
            # DNS rebinding window after the settings were saved).
            from app.features.ai.config import check_base_url

            err = check_base_url(self.base_url)
            if err:
                raise LLMError(err)
        started = time.monotonic()
        received = 0
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=TIMEOUT,
                follow_redirects=False,
            ) as http:
                with http.stream(
                    "POST",
                    _endpoint(self.base_url),
                    json=payload,
                    headers=self._headers(),
                ) as resp:
                    if resp.status_code >= 400:
                        self._read_limited(resp, started)
                        raise LLMError(
                            f"Nhà cung cấp AI trả lỗi {resp.status_code}: "
                            f"{_error_message(resp)}",
                            resp.status_code,
                        )
                    ctype = resp.headers.get("content-type", "")
                    if "text/event-stream" not in ctype:
                        # Some servers ignore stream=true and return
                        # the full JSON body.
                        self._read_limited(resp, started)
                        yield from self._from_json(resp)
                        return
                    for line in resp.iter_lines():
                        received += len(line) + 1
                        if received > MAX_BYTES:
                            raise LLMError(
                                "Phản hồi của nhà cung cấp AI quá lớn"
                            )
                        if time.monotonic() - started > MAX_SECONDS:
                            raise LLMError(
                                "Nhà cung cấp AI phản hồi quá lâu (timeout)"
                            )
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            err = chunk["error"]
                            detail = (
                                err.get("message")
                                if isinstance(err, dict)
                                else err
                            )
                            raise LLMError(
                                f"Nhà cung cấp AI trả lỗi: {detail}"
                            )
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                text_parts.append(delta["content"])
                                yield {
                                    "type": "text",
                                    "text": delta["content"],
                                }
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                slot = calls.setdefault(
                                    idx,
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {
                                            "name": "",
                                            "arguments": "",
                                        },
                                    },
                                )
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["function"]["arguments"] += fn[
                                        "arguments"
                                    ]
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
        except httpx.TimeoutException as e:
            raise LLMError("Nhà cung cấp AI phản hồi quá lâu (timeout)") from e
        except httpx.HTTPError as e:
            raise LLMError(
                f"Không kết nối được nhà cung cấp AI: {e.__class__.__name__}"
            ) from e
        message: dict = {
            "role": "assistant",
            "content": "".join(text_parts) or None,
        }
        if calls:
            tool_calls = []
            for i in sorted(calls):
                c = calls[i]
                c["id"] = c["id"] or f"call_{i}"
                tool_calls.append(c)
            message["tool_calls"] = tool_calls
        yield {
            "type": "done",
            "message": message,
            "finish_reason": finish,
            "usage": usage,
        }

    def _from_json(self, resp: httpx.Response) -> Iterator[dict]:
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError) as e:
            raise LLMError("Phản hồi của nhà cung cấp AI không hợp lệ") from e
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if content:
            yield {"type": "text", "text": content}
        message = {"role": "assistant", "content": content}
        if msg.get("tool_calls"):
            message["tool_calls"] = msg["tool_calls"]
        yield {
            "type": "done",
            "message": message,
            "finish_reason": choice.get("finish_reason"),
            "usage": body.get("usage"),
        }

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Call once and return the ``done`` event.

        The event holds ``message``, ``finish_reason`` and ``usage``.
        """
        for ev in self.stream(messages, tools, max_tokens):
            if ev["type"] == "done":
                return ev
        raise LLMError("Không nhận được phản hồi")


def client_for(config) -> LLMClient:
    """Create a client from an AiConfig.

    The address is checked (SSRF protection) before any call is made.
    """
    from app.features.ai.config import check_base_url

    err = check_base_url(config.base_url)
    if err:
        raise LLMError(err)
    return LLMClient(
        config.base_url, config.model, config.api_key, config.temperature
    )
