"""Airah AI chat endpoint for Vercel (Python serverless function).

POST /api/chat  {"message": "...", "language": "ur|en|auto", "system": "..."}
-> {"answer": "..."}

Needs the GROQ_API_KEY environment variable in Vercel
(Settings -> Environment Variables), then a redeploy.
Groq free tier, no card: openai/gpt-oss-20b, 1,000 requests/day.

api.groq.com sits behind Cloudflare, which blocks script-like
User-Agents with error 1010, so a browser-like User-Agent is required.
Only the Python standard library is used (no requirements.txt needed).
"""

import json
import os
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
    "Reply in the user's language (Urdu in Roman script if they write Roman Urdu)."
)


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
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"detail": "Invalid JSON body"})
            message = (data.get("message") or "").strip()
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
            payload = json.dumps(
                {
                    "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": message},
                    ],
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
