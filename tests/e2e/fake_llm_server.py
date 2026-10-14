"""Fake OpenAI Chat Completions-compatible LLM server for E2E tests.

Real HTTP, served from a thread. The scenario depends on the last
message:

- the last message is a tool result -> streamed text reply quoting
  figures from the tool;
- the user says "lập phiếu" -> call create_draft_cash_voucher;
- the user asks "tồn quỹ" -> call get_cash_balance;
- anything else -> a greeting.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DRAFT_DESCRIPTION = "Tạm ứng công tác phí (trợ lý AI E2E)"


def _reply(body: dict) -> tuple[str, list[tuple[str, dict]]]:
    msgs = body.get("messages") or []
    last = msgs[-1] if msgs else {}
    if last.get("role") == "tool":
        content = str(last.get("content") or "")
        try:
            data = json.loads(content)
        except ValueError:
            data = {}
        if isinstance(data, dict) and ("url" in data or "draft_id" in data):
            return (
                "Đã lập phiếu chi nháp, mời anh/chị kiểm tra: "
                f"{data.get('url', '')}",
                [],
            )
        return f"Kết quả tra cứu: {content[:200]}", []
    text = str(last.get("content") or "").lower()
    if "lập phiếu" in text:
        return "", [
            (
                "create_draft_cash_voucher",
                {
                    "kind": "payment",
                    "amount": 1500000,
                    "contra_account": "141",
                    "contact_name": "Nguyễn Văn Tạm Ứng",
                    "description": DRAFT_DESCRIPTION,
                },
            )
        ]
    if "tồn quỹ" in text:
        return "", [("get_cash_balance", {})]
    return "Xin chào, tôi là trợ lý kế toán (E2E).", []


def _chunks(text: str, calls: list[tuple[str, dict]], n: int) -> list[dict]:
    out = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
    for i in range(0, len(text), 8):
        out.append(
            {"choices": [{"index": 0, "delta": {"content": text[i : i + 8]}}]}
        )
    for i, (name, args) in enumerate(calls):
        raw = json.dumps(args, ensure_ascii=False)
        cut = len(raw) // 2
        out.append(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": i,
                                    "id": f"call_{n}_{i}",
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
        out.append(
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
    out.append(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "tool_calls" if calls else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 19,
            },
        }
    )
    return out


class FakeLLMServer:
    def __init__(self):
        self.requests: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, code: int, payload: dict) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    self._json(
                        200,
                        {
                            "object": "list",
                            "data": [{"id": "fake-e2e", "object": "model"}],
                        },
                    )
                else:
                    self._json(404, {"error": {"message": "not found"}})

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                server.requests.append(body)
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._json(404, {"error": {"message": "not found"}})
                    return
                text, calls = _reply(body)
                if not body.get("stream"):
                    msg = {"role": "assistant", "content": text or None}
                    if calls:
                        msg["tool_calls"] = [
                            {
                                "id": f"call_{i}",
                                "type": "function",
                                "function": {
                                    "name": c,
                                    "arguments": json.dumps(
                                        a, ensure_ascii=False
                                    ),
                                },
                            }
                            for i, (c, a) in enumerate(calls)
                        ]
                    self._json(
                        200,
                        {
                            "id": "chatcmpl-e2e",
                            "object": "chat.completion",
                            "model": "fake-e2e",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": msg,
                                    "finish_reason": "tool_calls"
                                    if calls
                                    else "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 12,
                                "completion_tokens": 7,
                                "total_tokens": 19,
                            },
                        },
                    )
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.end_headers()
                for c in _chunks(text, calls, len(server.requests)):
                    chunk = json.dumps(c, ensure_ascii=False)
                    self.wfile.write(f"data: {chunk}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )

    def start(self) -> FakeLLMServer:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
