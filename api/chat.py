"""Airah AI chat endpoint for Vercel (Python serverless function).

POST /api/chat  {"message": "...", "language": "ur|en|auto", "system": "...",
                 "history": [{"role": "user"|"assistant", "content": "..."}]}
-> {"answer": "..."}

`history` is OPTIONAL (Step 1 of the Nov 2026 upgrade): when present, the
last 20 messages are sent to the provider before the new message, so the
assistant remembers the conversation. Without it the endpoint behaves
exactly as before.

Rate limit (Step 2): when UPSTASH_REDIS_REST_URL and
UPSTASH_REDIS_REST_TOKEN are set in Vercel env vars, each IP is limited to
20 requests per 60 seconds; over the limit the endpoint answers 429 with
{"detail": "rate_limited"}. Without those vars (or on any Upstash error)
the check is skipped — fail-open, never blocks real users.

Needs the GROQ_API_KEY environment variable in Vercel
(Settings -> Environment Variables), then a redeploy.
Groq free tier, no card: openai/gpt-oss-20b, 1,000 requests/day.

api.groq.com sits behind Cloudflare, which blocks script-like
User-Agents with error 1010, so a browser-like User-Agent is required.
Only the Python standard library is used (no requirements.txt needed).
"""

import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_SYSTEM = (
    "You are Airah, a helpful multilingual personal AI assistant. "
    "Reply in the user's language. "
    "Use Pakistani Urdu only — never use Hindi words or Hindi phrasing "
    "(never write words like 'yadi', 'kripya', 'samay'). "
    "If the user writes in Roman Urdu, reply in Roman Urdu using Pakistani "
    "Urdu words. If the user writes in Urdu script, reply in Urdu script."
)

MAX_MSG = 4000  # characters per message
MAX_HISTORY = 20  # last N messages kept
RATE_LIMIT = 20  # requests per window, per IP
RATE_WINDOW = 60  # seconds


def clean_history(history):
    out = []
    for m in (history or [])[-MAX_HISTORY:]:
        role = m.get("role") if isinstance(m, dict) else None
        text = str(m.get("content", ""))[:MAX_MSG] if isinstance(m, dict) else ""
        if role in ("user", "assistant") and text.strip():
            out.append({"role": role, "content": text})
    return out


def clean_message(message):
    return str(message or "").strip()[:MAX_MSG]


def _client_ip(handler):
    return (
        (handler.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or "unknown"
    )


def is_limited(ip):
    """True when this IP passed RATE_LIMIT requests in the current window.

    Upstash Redis REST (free tier). Fail-open: without env vars, or on any
    error, returns False. Upstash returns INCR results as strings, so the
    count is cast to int before comparing.
    """
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip()
    tok = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not tok:
        return False
    key = "rl:%s:%d" % (ip, int(time.time() // RATE_WINDOW))
    try:
        payload = json.dumps([["INCR", key], ["EXPIRE", key, RATE_WINDOW]]).encode(
            "utf-8"
        )
        req = urllib.request.Request(
            url.rstrip("/") + "/pipeline",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + tok,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            out = json.loads(resp.read())
        return int(out[0].get("result", 0)) > RATE_LIMIT
    except Exception:
        return False


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {})

    def do_POST(self):
        try:
            if is_limited(_client_ip(self)):
                return self._send(429, {"detail": "rate_limited"})
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"detail": "Invalid JSON body"})
            message = clean_message(data.get("message"))
            if not message:
                return self._send(400, {"detail": "message is required"})

            key = os.environ.get("GROQ_API_KEY", "").strip()
            if not key:
                return self._send(
                    500,
                    {
                        "detail": (
                            "GROQ_API_KEY is not set on the server. "
                            "Add it in Vercel Settings -> Environment Variables, "
                            "then redeploy."
                        )
                    },
                )

            system = data.get("system") or DEFAULT_SYSTEM
            messages = (
                [{"role": "system", "content": system}]
                + clean_history(data.get("history"))
                + [{"role": "user", "content": message}]
            )
            payload = json.dumps(
                {
                    "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
                    "messages": messages,
                    "temperature": 0.7,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                GROQ_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key,
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read())
                    detail = err.get("error", {}).get("message", str(e))
                except Exception:
                    detail = str(e)
                return self._send(500, {"detail": detail})

            answer = out["choices"][0]["message"]["content"]
            return self._send(200, {"answer": answer})
        except Exception as e:  # noqa: BLE001 - surface as JSON, never crash
            return self._send(500, {"detail": str(e)})
