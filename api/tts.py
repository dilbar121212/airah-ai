"""Airah AI voice endpoint for Vercel (Python serverless function).

POST /api/tts  {"text": "...", "language": "ur|roman-ur|en|hi|ar",
                "voice": "ur-PK-UzmaNeural"}
-> audio/mpeg bytes (MP3)

Uses Microsoft Edge's free neural TTS (edge-tts, no API key).

Rules this endpoint enforces (Urdu voice fix, Nov 2026):
- The voice must match the language. A voice that does not belong to the
  requested language is rejected with HTTP 400 — never silently swapped
  for another language's voice (e.g. Urdu text is never read by the
  English voice, and English text is never read by the Urdu voice).
- Roman Urdu is transliterated to Urdu script before synthesis, because
  the Urdu neural voice is trained on Urdu script; feeding it Latin
  script directly produces the garbled "Hindi/Bengali-like" sound.
- Long replies are split into sentences and synthesised concurrently,
  then joined — so the voice no longer cuts off mid-sentence at 600 chars.
  Total text is capped at 2000 chars (serverless time limit).
"""

import asyncio
import json
import re
from http.server import BaseHTTPRequestHandler

import edge_tts

VOICE_BY_LANG = {
    "ur": ["ur-PK-UzmaNeural", "ur-PK-AsadNeural"],
    "roman-ur": ["ur-PK-UzmaNeural", "ur-PK-AsadNeural"],
    "en": ["en-US-AriaNeural"],
    "hi": ["hi-IN-SwaraNeural"],
    "ar": ["ar-SA-ZariyahNeural"],
}
DEFAULT_VOICE = {
    "ur": "ur-PK-UzmaNeural",
    "roman-ur": "ur-PK-UzmaNeural",
    "en": "en-US-AriaNeural",
    "hi": "hi-IN-SwaraNeural",
    "ar": "ar-SA-ZariyahNeural",
}
VOICE_TO_LANG = {}
for _lang, _voices in VOICE_BY_LANG.items():
    for _v in _voices:
        VOICE_TO_LANG.setdefault(_v, _lang)  # first mapping wins: Uzma -> 'ur'

MAX_TOTAL_CHARS = 2000
MAX_CHUNK_CHARS = 250

# --- Roman Urdu -> Urdu script (common words; unknown words pass through) ---
PHRASES = {
    "assalam-o-alaikum": "السلام علیکم",
    "assalam o alaikum": "السلام علیکم",
    "wa alaikum assalam": "وعلیکم السلام",
    "waalaikum assalam": "وعلیکم السلام",
    "khuda hafiz": "خدا حافظ",
    "allah hafiz": "اللہ حافظ",
    "khuda hafiz": "خدا حافظ",
}

ROMAN_TO_URDU = {
    "salam": "سلام", "shukriya": "شکریہ", "meherbani": "مہربانی",
    "khuda": "خدا", "hafiz": "حافظ", "allah": "اللہ",
    "main": "میں", "mein": "میں", "mujhe": "مجھے", "mujh": "مجھ",
    "mera": "میرا", "meri": "میری", "mere": "میرے",
    "tum": "تم", "tumhara": "تمہارا", "tumhari": "تمہاری", "tumhare": "تمہارے",
    "aap": "آپ", "aapka": "آپ کا", "aapki": "آپ کی", "aapko": "آپ کو",
    "hum": "ہم", "hamara": "ہمارا", "hamari": "ہماری",
    "yeh": "یہ", "ye": "یہ", "woh": "وہ", "wo": "وہ",
    "us": "اس", "uska": "اس کا", "uski": "اس کی", "is": "اس",
    "iska": "اس کا", "iski": "اس کی", "in": "ان", "un": "ان",
    "kya": "کیا", "kaisa": "کیسا", "kaisi": "کیسی", "kaise": "کیسے",
    "kab": "کب", "kahan": "کہاں", "kidhar": "کدھر", "kaun": "کون",
    "kyun": "کیوں", "kyunke": "کیونکہ",
    "ka": "کا", "ki": "کی", "ke": "کے", "se": "سے",
    "par": "پر", "ko": "کو", "ne": "نے",
    "hai": "ہے", "hain": "ہیں", "ho": "ہو", "hun": "ہوں", "hoon": "ہوں",
    "tha": "تھا", "thi": "تھی", "the": "تھے",
    "hoga": "ہوگا", "hogi": "ہوگی", "honge": "ہوں گے",
    "karo": "کرو", "karna": "کرنا", "kar": "کر", "kiya": "کیا",
    "ki": "کی", "kiye": "کیے", "kare": "کرے", "karein": "کریں",
    "kardo": "کردو", "kar do": "کردو",
    "jao": "جاؤ", "jana": "جانا", "gaya": "گیا", "gayi": "گئی",
    "aao": "آؤ", "aana": "آنا", "aaya": "آیا", "aayi": "آئی",
    "bolo": "بولو", "bolna": "بولنا", "bola": "بولا", "boli": "بولی",
    "suno": "سنو", "sunna": "سننا", "suna": "سنا",
    "dekho": "دیکھو", "dekhna": "دیکھنا", "dekha": "دیکھا",
    "likho": "لکھو", "likhna": "لکھنا", "likha": "لکھا",
    "parho": "پڑھو", "parhna": "پڑھنا", "parha": "پڑھا",
    "samjho": "سمجھو", "samajh": "سمجھ", "samjha": "سمجھا",
    "batao": "بتاؤ", "batana": "بتانا", "bataya": "بتایا",
    "poochho": "پوچھو", "poochna": "پوچھنا", "poocha": "پوچھا",
    "chahiye": "چاہیے", "chahta": "چاہتا", "chahti": "چاہتی",
    "chahte": "چاہتے", "sakta": "سکتا", "sakti": "سکتی", "sakte": "سکتے",
    "aaj": "آج", "kal": "کل", "abhi": "ابھی", "ab": "اب",
    "phir": "پھر", "bhi": "بھی", "nahi": "نہیں", "nahin": "نہیں",
    "na": "نہ", "aur": "اور", "lekin": "لیکن", "magar": "مگر",
    "agar": "اگر", "to": "تو", "jo": "جو", "jab": "جب", "tab": "تب",
    "bahut": "بہت", "bohot": "بہت", "bohat": "بہت",
    "acha": "اچھا", "achha": "اچھا", "accha": "اچھا",
    "theek": "ٹھیک", "bilkul": "بالکل", "zaroor": "ضرور",
    "shayad": "شاید", "aksar": "اکثر", "kabhi": "کبھی",
    "hamesha": "ہمیشہ", "roz": "روز", "har": "ہر", "sab": "سب",
    "kuch": "کچھ", "koi": "کوئی", "kisi": "کسی",
    "ghar": "گھر", "kaam": "کام", "baat": "بات", "dil": "دل",
    "dost": "دوست", "bhai": "بھائی", "behn": "بہن", "behen": "بہن",
    "ammi": "امی", "abba": "ابا", "abbu": "ابو",
    "beta": "بیٹا", "beti": "بیٹی", "maa": "ماں", "baap": "باپ",
    "baccha": "بچہ", "bacche": "بچے", "admi": "آدمی", "aurat": "عورت",
    "larka": "لڑکا", "larki": "لڑکی", "shadi": "شادی",
    "khana": "کھانا", "pani": "پانی", "chai": "چائے", "doodh": "دودھ",
    "waqt": "وقت", "din": "دن", "raat": "رات", "subah": "صبح",
    "shaam": "شام", "dopahar": "دوپہر", "saal": "سال",
    "mahina": "مہینہ", "hafta": "ہفتہ", "paisa": "پیسہ",
    "rupay": "روپے", "mobile": "موبائل", "phone": "فون",
    "number": "نمبر", "message": "پیغام", "khabar": "خبر",
    "madad": "مدد", "dua": "دعا", "sehat": "صحت", "doctor": "ڈاکٹر",
    "school": "اسکول", "kitab": "کتاب", "sawal": "سوال",
    "jawab": "جواب", "naam": "نام", "jagah": "جگہ",
    "rasta": "راستہ", "sheher": "شہر", "mulk": "ملک",
    "pakistan": "پاکستان", "urdu": "اردو", "english": "انگریزی",
    "bada": "بڑا", "chota": "چھوٹا", "bura": "برا",
    "naya": "نیا", "purana": "پرانا", "garam": "گرم",
    "thanda": "ٹھنڈا", "tez": "تیز", "ahista": "آہستہ",
    "jaldi": "جلدی", "der": "دیر", "pehle": "پہلے", "baad": "بعد",
    "upar": "اوپر", "neeche": "نیچے", "andar": "اندر",
    "bahar": "باہر", "sath": "ساتھ", "saath": "ساتھ",
    "paas": "پاس", "door": "دور", "qareeb": "قریب",
    "khush": "خوش", "khushi": "خوشی", "gham": "غم",
    "pyar": "پیار", "mohabbat": "محبت", "maaf": "معاف",
    "mashallah": "ماشاءاللہ", "inshallah": "انشاءاللہ",
    "alhamdulillah": "الحمدللہ", "jazakallah": "جزاک اللہ",
    # Weather words (Airah weather answers, Roman Urdu -> Urdu script).
    "saaf": "صاف", "aasmaan": "آسمان", "asman": "آسمان",
    "badal": "بادل", "barish": "بارش", "halki": "ہلکی",
    "dhund": "دھند", "boondabandi": "بونداباندی", "bochhar": "بوچھاڑ",
    "toofan": "طوفان", "garaj": "گرج", "chamak": "چمک",
    "barf": "برف", "chhaye": "چھائے", "hue": "ہوئے",
    "mehsoos": "محسوس", "nami": "نمی", "hawa": "ہوا",
    "kilometer": "کلومیٹر", "ghanta": "گھنٹہ", "ghante": "گھنٹے",
    "feesad": "فیصد", "degree": "ڈگری", "zyada": "زیادہ",
    "kam": "کم", "sardi": "سردی", "garmi": "گرمی",
    "thand": "ٹھنڈ", "mausam": "موسم", "sheher": "شہر",
    "olay": "اولے", "dane": "دانے", "agley": "اگلے",
    "ghanton": "گھنٹوں", "dinon": "دنوں", "chance": "امکان",
    "fi": "فی",
}


def _clean(text):
    text = re.sub(r"[*#_`>]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


DETECT_WORDS = set(ROMAN_TO_URDU) - {"is", "in", "to", "pakistan"}


def _detect_language(text):
    """ur (Urdu script) / hi (Devanagari) / roman-ur / en. No guessing:
    Latin text is Roman Urdu only when a good share of its words are
    common Urdu words; otherwise it is English."""
    if re.search(r"[\u0600-\u06FF]", text):
        return "ur"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    words = re.findall(r"[a-z]+", text.lower())
    if words:
        # "is", "in", "to", "pakistan" are also plain English words, so they
        # must not count toward a Roman-Urdu detection.
        hits = sum(1 for w in words if w in DETECT_WORDS)
        if hits / len(words) >= 0.25:
            return "roman-ur"
    return "en"


def _roman_to_urdu(text):
    t = text
    for phrase, urdu in PHRASES.items():
        t = re.sub(r"\b" + re.escape(phrase) + r"\b", urdu, t, flags=re.I)

    def sub(m):
        return ROMAN_TO_URDU.get(m.group(0).lower(), m.group(0))

    return re.sub(r"[A-Za-z]+", sub, t)


_SENTENCE_END = re.compile(r"(?<=[.!?۔؟\n])\s+")


def _split_chunks(text, max_chars=MAX_CHUNK_CHARS):
    """Split into sentence-aware chunks so long replies are read in full
    instead of being cut off mid-sentence."""
    chunks, cur = [], ""
    for part in _SENTENCE_END.split(text):
        part = part.strip()
        if not part:
            continue
        if len(cur) + len(part) + 1 <= max_chars:
            cur = (cur + " " + part).strip() if cur else part
        else:
            if cur:
                chunks.append(cur)
            while len(part) > max_chars:
                chunks.append(part[:max_chars])
                part = part[max_chars:]
            cur = part
    if cur:
        chunks.append(cur)
    return chunks


async def _synth_all(text, voice):
    chunks = _split_chunks(text)
    sem = asyncio.Semaphore(4)

    async def one(chunk):
        async with sem:
            parts = []
            async for part in edge_tts.Communicate(chunk, voice).stream():
                if part["type"] == "audio":
                    parts.append(part["data"])
            return b"".join(parts)

    audios = await asyncio.gather(*[one(c) for c in chunks])
    return b"".join(audios)


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
            text = text[:MAX_TOTAL_CHARS]

            language = (data.get("language") or "").strip().lower()
            voice = (data.get("voice") or "").strip()
            if language and language not in VOICE_BY_LANG:
                language = ""
            if not language and voice in VOICE_TO_LANG:
                language = VOICE_TO_LANG[voice]
            if not language:
                language = _detect_language(text)
            if voice:
                # A voice that does not belong to the language is an error,
                # never a silent swap to another language's voice.
                if voice not in VOICE_BY_LANG[language]:
                    return self._send_json(
                        400,
                        {"detail": "voice " + voice + " does not match language " + language},
                    )
            else:
                voice = DEFAULT_VOICE[language]
            if language == "roman-ur":
                text = _roman_to_urdu(text)
            audio = asyncio.run(_synth_all(text, voice))
            if not audio:
                return self._send_json(500, {"detail": "No audio generated"})
            return self._send_audio(audio)
        except Exception as e:  # noqa: BLE001 - surface as JSON, never crash
            return self._send_json(500, {"detail": str(e)})
