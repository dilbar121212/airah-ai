"""Airah AI WhatsApp webhook for Vercel (Python serverless function).

Meta WhatsApp Cloud API webhook:

  GET  /api/whatsapp?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
       -> returns hub.challenge as plain text when the verify token matches
          WHATSAPP_VERIFY_TOKEN (this is Meta's one-time webhook check).
  POST /api/whatsapp  (Meta sends incoming messages here)
       -> for each incoming text message: ask Groq (same brain as /api/chat),
          then reply through the WhatsApp Cloud API.

Env vars needed in Vercel (Settings -> Environment Variables), then redeploy:
  GROQ_API_KEY (already set)
  WHATSAPP_TOKEN (the WhatsApp Cloud API access token, Bearer token from
    Meta App Dashboard -> WhatsApp -> API Setup)
  WHATSAPP_PHONE_NUMBER_ID (the sending number's ID, same dashboard page)
  WHATSAPP_VERIFY_TOKEN (any random string you choose; the same value goes
    in the Meta webhook "Verify Token" field)

Meta webhook setup (Meta App Dashboard -> WhatsApp -> Configuration):
  Callback URL: https://airah-ai.vercel.app/api/whatsapp
  Verify Token: the WHATSAPP_VERIFY_TOKEN value
  Subscribe to the "messages" webhook field.

Limits: text messages only for now (voice notes and images get a short
"abhi sirf text" reply). Replies go to people who message the WhatsApp
Business number first, so they are inside Meta's 24-hour window; template
messages are not used.

Only the Python standard library is used (no requirements.txt needed).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GRAPH_URL = "https://graph.facebook.com/v21.0"
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
    "Urdu words. If the user writes in Urdu script, reply in Urdu script. "
    "Keep replies short and chatty, like WhatsApp messages — a few lines, "
    "not long essays."
)
MAX_REPLY_CHARS = 4000


def ask_groq(message, key):
    payload = json.dumps(
        {
            "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
            "messages": [
                {"role": "system", "content": DEFAULT_SYSTEM},
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
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read())
    return out["choices"][0]["message"]["content"]


def send_whatsapp(to, text, token, phone_number_id):
    payload = json.dumps(
        {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text[:MAX_REPLY_CHARS]},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GRAPH_URL + "/" + phone_number_id + "/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


class handler(BaseHTTPRequestHandler):
    def _send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Meta's webhook verification handshake.
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query
        )
        mode = (query.get("hub.mode") or [""])[0]
        token = (query.get("hub.verify_token") or [""])[0]
        challenge = (query.get("hub.challenge") or [""])[0]
        expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        if mode == "subscribe" and token and expected and token == expected:
            return self._send_text(200, challenge)
        return self._send_text(403, "Verification failed")

    def do_POST(self):
        # Always answer Meta with 200 so it does not retry the webhook.
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._send_json(200, {"status": "ok"})

            groq_key = os.environ.get("GROQ_API_KEY", "").strip()
            wa_token = os.environ.get("WHATSAPP_TOKEN", "").strip()
            phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
            if not groq_key or not wa_token or not phone_id:
                print("whatsapp webhook: missing env vars")
                return self._send_json(200, {"status": "ok"})

            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    for msg in value.get("messages", []):
                        sender = msg.get("from", "")
                        if not sender:
                            continue
                        if msg.get("type") == "text":
                            text = (msg.get("text") or {}).get("body", "")
                            if not text.strip():
                                continue
                            try:
                                answer = ask_groq(text.strip(), groq_key)
                            except Exception as e:  # noqa: BLE001
                                print("groq error: " + str(e))
                                answer = (
                                    "Maaf kijiye, abhi jawab nahi de saki — "
                                    "thodi der baad dobara message karein."
                                )
                        else:
                            answer = (
                                "Abhi main sirf text message parh sakti hoon — "
                                "apna sawal likh kar bhejein."
                            )
                        try:
                            send_whatsapp(sender, answer, wa_token, phone_id)
                        except Exception as e:  # noqa: BLE001
                            print("whatsapp send error: " + str(e))
            return self._send_json(200, {"status": "ok"})
        except Exception as e:  # noqa: BLE001 - never crash the webhook
            print("whatsapp webhook error: " + str(e))
            try:
                return self._send_json(200, {"status": "ok"})
            except Exception:
                return None
