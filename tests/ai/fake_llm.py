"""Fake LLM server for tests (OpenAI Chat Completions, SSE streaming).

Makes no real network calls.
"""

from __future__ import annotations

import json

import httpx

from app.features.ai import client as ai_client


class FakeLLM:
    """Fake LLM that replays scripted responses.

    responses: list of responses in call order, each one of::

        {"text": "..."}
        {"tool_calls": [(name, args_dict_or_json_str)], "text": "..."}
        {"status": 401, "error": "..."}

    When the list runs out it replies "Xong.".
    """

    def __init__(self, responses=None, *, stream: bool = True):
        self.responses = list(responses or [])
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.stream = stream

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        resp = self.responses.pop(0) if self.responses else {"text": "Xong."}
        if resp.get("status"):
            return httpx.Response(
                resp["status"],
                json={"error": {"message": resp.get("error", "lỗi")}},
            )
        calls = resp.get("tool_calls") or []
        text = resp.get("text") or ""
        if not self.stream:
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": n,
                            "arguments": a
                            if isinstance(a, str)
                            else json.dumps(a),
                        },
                    }
                    for i, (n, a) in enumerate(calls)
                ]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "index": 0,
                            "message": msg,
                            "finish_reason": "tool_calls" if calls else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )
        chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
        if text:
            half = len(text) // 2
            for part in (text[:half], text[half:]):
                if part:
                    chunks.append(
                        {"choices": [{"index": 0, "delta": {"content": part}}]}
                    )
        for i, (name, args) in enumerate(calls):
            raw = (
                args
                if isinstance(args, str)
                else json.dumps(args, ensure_ascii=False)
            )
            cut = len(raw) // 2
            chunks.append(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": i,
                                        "id": f"call_{len(self.requests)}_{i}",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": raw[:cut],
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
            chunks.append(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": i,
                                        "function": {"arguments": raw[cut:]},
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
        chunks.append(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if calls else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        )
        sse = (
            "".join(
                f"data: {json.dumps(c, ensure_ascii=False)}\n\n"
                for c in chunks
            )
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse.encode(),
        )

    def install(self) -> FakeLLM:
        ai_client.set_transport(httpx.MockTransport(self.handler))
        return self


def enable_ai(db, ctx, **overrides) -> None:
    from app.features.ai.config import save_config

    data = {
        "enabled": True,
        "preset": "custom",
        "base_url": "https://llm.test/v1",
        "model": "fake-model",
        "api_key": "sk-test-123456789",
        "temperature": 0.1,
        "max_rounds": 4,
        "daily_limit": 100,
    }
    data.update(overrides)
    save_config(db, ctx, data)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        payload = "\n".join(data)
        out.append(
            (
                event,
                json.loads(payload)
                if payload and payload != "[DONE]"
                else {"raw": payload},
            )
        )
    return out
