"""Airah AI voice endpoint for Vercel (Python serverless function).

POST /api/tts  {"text": "...", "voice": "ur-PK-UzmaNeural"}
-> audio/mpeg bytes (MP3)

Uses Microsoft Edge's free neural TTS (edge-tts, no API key).
Voices: ur-PK-UzmaNeural (female, default), ur-PK-AsadNeural (male),
en-US-AriaNeural (English female). Text is capped so a reply
synthesises well inside the serverless time limit.
"""

import asyncio
import json
import re
from http.server import BaseHTTPRequestHandler

import edge_tts

VOICES = {
    "female": "ur-PK-UzmaNeural",
    "male": "ur-PK-AsadNeural",
    "en": "en-US-AriaNeural",
}
MAX_CHARS = 600


def _clean(text):
    text = re.sub(r"[*#_`>]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _synth(text, voice):
    chunks = []
    async for part in edge_tts.Communicate(text, voice).stream():
        if part["type"] == "audio":
            chunks.append(part["data"])
    return b"".join(chunks)


class handler(BaseHTTPRequestHandler):
    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_audio(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send_json(200, {})

    def do_GET(self):
        self._send_json(405, {"detail": "Use POST with {\"text\": \"...\"}"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._send_json(400, {"detail": "Invalid JSON body"})
            text = _clean(data.get("text") or "")
            if not text:
                return self._send_json(400, {"detail": "text is required"})
            text = text[:MAX_CHARS]
            voice = data.get("voice") or VOICES["female"]
            if voice not in VOICES.values():
                voice = VOICES["female"]
            audio = asyncio.run(_synth(text, voice))
            if not audio:
                return self._send_json(500, {"detail": "No audio generated"})
            return self._send_audio(audio)
        except Exception as e:  # noqa: BLE001 - surface as JSON, never crash
            return self._send_json(500, {"detail": str(e)})
