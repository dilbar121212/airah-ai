"""Airah AI news endpoint for Vercel (Python serverless function).

GET /api/news?cat=pakistan|local|world|technology|sports|business&city=Karachi
GET /api/news?cat=trends&geo=PK|US
-> {"updated_at": "...", "stale": false, "items": [
     {"id","title_ur","title_en","summary_ur","source","time","link",
      "category","region","type"}]}

News source: Google News RSS (free, no key, no account). Headlines are
translated to natural Pakistani Urdu in ONE Groq call (same free key as
/api/chat); if translation is unavailable the original headline is
returned with translated:false - never invented.

Google Trends: the public daily-trends RSS feed
(trends.google.com/trendingsearches/daily/rss) and the dailytrends API
both return 404 as of 27 Nov 2026 - Google has retired them. The endpoint
still tries them and, when they fail, returns trends_available:false with
an honest message instead of fabricated trends.

Only the Python standard library is used. Responses carry
Cache-Control s-maxage=600 so Vercel caches them for 10 minutes, and a
stale in-memory copy is served when Google rate-limits (HTTP 429).
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
NEWS_BASE = "https://news.google.com/rss"
TRENDS_RSS = "https://trends.google.com/trends/trendingsearches/daily/rss"
TRENDS_API = "https://trends.google.com/trends/api/dailytrends"
CACHE_TTL = 600  # seconds

_cache = {}  # key -> (expires_at, payload)


def fetch_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def clean_text(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_urdu(text):
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def norm_key(title, link):
    t = re.sub(r"[^a-z0-9\u0600-\u06FF]+", "", (title or "").lower())
    return t[:60] + "|" + (link or "")[:80]


def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = clean_text(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        desc = clean_text(it.findtext("description"))
        src_el = it.find("source")
        source = clean_text(src_el.text) if src_el is not None else ""
        if not title or not link:
            continue
        # Google News descriptions often repeat the title; drop the repeat.
        if desc.startswith(title):
            desc = desc[len(title):].strip(" -–—")
        items.append(
            {
                "title": title,
                "link": link,
                "time": pub,
                "desc": desc[:220],
                "source": source,
            }
        )
    return items


def google_news_url(query=None, hl="en", gl="US"):
    ceid = gl + ":" + hl
    if query:
        q = {"q": query, "hl": hl, "gl": gl, "ceid": ceid}
        return NEWS_BASE + "/search?" + urllib.parse.urlencode(q)
    return NEWS_BASE + "?" + urllib.parse.urlencode(
        {"hl": hl, "gl": gl, "ceid": ceid}
    )


def feed_urls(cat, city):
    """(list of RSS urls, region) for one category."""
    if cat == "local":
        city = city or "Karachi"
        return [
            google_news_url(city, "ur", "PK"),
            google_news_url(city, "en", "US"),
        ], "local"
    if cat == "pakistan":
        return [
            google_news_url("Pakistan", "ur", "PK"),
            google_news_url("Pakistan", "en", "US"),
        ], "pakistan"
    if cat == "world":
        return [google_news_url(None, "en", "US")], "world"
    if cat in ("technology", "sports", "business"):
        return [
            google_news_url(cat, "en", "US"),
            google_news_url(cat, "ur", "PK"),
        ], "world"
    return [google_news_url(None, "en", "US")], "world"


def translate_batch(items, debug=None):
    """Translate Latin-script titles+descs to Pakistani Urdu in one Groq call.

    Returns {index: (title_ur, summary_ur)}. Empty dict on any failure.
    When debug is a dict, failure details are recorded in it (no secrets).
    """
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if debug is not None:
        debug["groq_key_present"] = bool(key)
    if not key:
        if debug is not None:
            debug["error"] = "no key"
        return {}
    todo = [
        (i, it)
        for i, it in enumerate(items)
        if not is_urdu(it["title"])
    ]
    if not todo:
        return {}
    result = {}
    # Translate in small batches of 6. Notes from live testing 27 Nov 2026:
    # - one 12-item call returned empty content; 3- and 6-item calls work.
    # - response_format json_object makes the model ECHO the English titles
    #   (only summaries come back in Urdu), so plain mode is used instead.
    # - even in plain mode the model SOMETIMES echoes the English title
    #   while translating the summary, so echoed titles are retried below.
    for start in range(0, len(todo), 6):
        chunk = todo[start:start + 6]
        result.update(_translate_chunk(chunk, key, debug))
    # On a 429 (Groq per-minute token limit) stop retrying: more calls
    # would only burn the shared quota. The app falls back to English.
    if debug is not None and debug.get("groq_http") == 429:
        return result
    # Retry any index the model dropped (partial arrays happen).
    missing = [t for t in todo if t[0] not in result]
    for start in range(0, len(missing), 3):
        result.update(_translate_chunk(missing[start:start + 3], key, debug))
    # Retry any title the model echoed back in Latin script.
    echoed = [
        (i, items[i]["title"])
        for i in result
        if not is_urdu(result[i][0])
    ]
    if echoed:
        fixed = _translate_titles_only(echoed, key, debug)
        for i, t in fixed.items():
            if is_urdu(t):
                result[i] = (t, result[i][1])
    return result


def _translate_titles_only(pairs, key, debug=None):
    """Translate bare headlines to Urdu script, one per line, no JSON.

    Used when the batch translator echoes an English title. Returns
    {index: urdu_title}. Empty dict on any failure.
    """
    lines = "\n".join(t for _, t in pairs)
    prompt = (
        "Translate each headline below to natural Pakistani Urdu. "
        "Keep people's names, place names, organisation names and numbers "
        "exactly as they are. Reply with ONLY the Urdu headlines, one per "
        "line, in the same order, no numbering, no English, no extra text.\n"
        + lines
    )
    body = json.dumps(
        {
            "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
            "messages": [
                {"role": "system", "content": "You translate news headlines into Pakistani Urdu. Reply with Urdu lines only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL,
        data=body,
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
        text = out["choices"][0]["message"].get("content") or ""
    except Exception as e:
        if debug is not None:
            debug["titles_retry_error"] = type(e).__name__ + ": " + str(e)[:150]
        return {}
    got = [ln.strip().strip("\"'").strip() for ln in text.strip().splitlines() if ln.strip()]
    result = {}
    for (i, _), t in zip(pairs, got):
        if t:
            result[i] = t
    return result


def _translate_chunk(chunk, key, debug=None):
    payload_items = [
        {"i": i, "title": it["title"], "desc": it["desc"]} for i, it in chunk
    ]
    prompt = (
        "Translate each news headline and short description to natural "
        "Pakistani Urdu. The title field MUST be written in Urdu script, "
        "never in English - for example 'Gold price rises in Pakistan' "
        "becomes 'پاکستان میں سونے کی قیمت بڑھ گئی'. Keep people's names, "
        "place names, organisation names and numbers exactly as they are. "
        "Reply with ONLY a JSON array like "
        "[{\"i\": 0, \"title\": \"...\", \"summary\": \"...\"}] - one line per "
        "item, summary in Urdu, max 20 words. No other text.\n"
        + json.dumps(payload_items, ensure_ascii=False)
    )
    body = json.dumps(
        {
            "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
            "messages": [
                {"role": "system", "content": "You translate news into Pakistani Urdu. Reply with JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL,
        data=body,
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
        msg = out["choices"][0]["message"]
        text = msg.get("content") or ""
        if not text and msg.get("reasoning_content"):
            text = msg.get("reasoning_content") or ""
    except urllib.error.HTTPError as e:
        if debug is not None:
            try:
                debug["groq_http"] = e.code
                debug["groq_body"] = e.read()[:300].decode("utf-8", "replace")
            except Exception:
                debug["groq_http"] = e.code
        text = ""
    except Exception as e:
        if debug is not None:
            debug["error"] = type(e).__name__ + ": " + str(e)[:200]
        text = ""
    if debug is not None and text:
        debug["resp_len"] = len(text)
    arr = []
    if text:
        try:
            parsed = json.loads(text)
            arr = parsed.get("items") if isinstance(parsed, dict) else parsed
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", text, re.S)
            if m:
                try:
                    arr = json.loads(m.group(0))
                except json.JSONDecodeError as e:
                    if debug is not None:
                        debug["error"] = "json parse: " + str(e)
    result = {}
    for entry in arr or []:
        try:
            i = int(entry["i"])
            t = (entry.get("title") or "").strip()
            s = (entry.get("summary") or "").strip()
            if t:
                result[i] = (t, s)
        except (KeyError, TypeError, ValueError):
            continue
    if result:
        return result
    # Fallback: split the chunk in half and retry each half (not on 429).
    if debug is not None and debug.get("groq_http") == 429:
        return {}
    if len(chunk) > 3:
        half = len(chunk) // 2
        out = {}
        out.update(_translate_chunk(chunk[:half], key, debug))
        out.update(_translate_chunk(chunk[half:], key, debug))
        return out
    return {}


def fetch_trends(geo):
    """Try the public Google Trends feeds. Returns (available, trends, note)."""
    urls = [
        TRENDS_RSS + "?" + urllib.parse.urlencode({"geo": geo}),
        TRENDS_API + "?" + urllib.parse.urlencode(
            {"hl": "en-US", "tz": "300", "geo": geo, "ns": "15"}
        ),
    ]
    for url in urls:
        try:
            raw = fetch_text(url, timeout=20)
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue
        if url.startswith(TRENDS_RSS):
            trends = []
            for it in parse_rss(raw):
                trends.append({"title": it["title"], "link": it["link"], "time": it["time"]})
            if trends:
                return True, trends[:10], ""
        else:
            try:
                data = json.loads(raw[5:] if raw.startswith(")]}'") else raw)
                days = data.get("default", {}).get("trendingSearchesDays", [])
                trends = []
                for day in days:
                    for s in day.get("trendingSearches", []):
                        title = s.get("title", {}).get("query", "")
                        if title:
                            trends.append({"title": title, "link": "https://trends.google.com/trends/explore?q=" + urllib.parse.quote(title), "time": day.get("date", "")})
                if trends:
                    return True, trends[:10], ""
            except Exception:
                continue
    return (
        False,
        [],
        "Google Trends ka public feed abhi nahi mil raha (Google ne ye feed band kar di hai).",
    )


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "public, s-maxage=600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {})

    def do_GET(self):
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            cat = (qs.get("cat", ["pakistan"])[0] or "pakistan").strip().lower()
            city = (qs.get("city", [""])[0] or "").strip()
            geo = (qs.get("geo", ["PK"])[0] or "PK").strip().upper()
            debug = {} if qs.get("debug") else None

            if cat == "trends":
                key = "trends:" + geo
                now = time.time()
                if key in _cache and _cache[key][0] > now:
                    payload = dict(_cache[key][1])
                    payload["cached"] = True
                    return self._send(200, payload)
                available, trends, note = fetch_trends(geo)
                payload = {
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "trends_available": available,
                    "trends": trends,
                    "note": note,
                    "cached": False,
                    "stale": False,
                }
                _cache[key] = (now + CACHE_TTL, payload)
                return self._send(200, payload)

            key = "news:%s:%s" % (cat, city)
            now = time.time()
            urls, region = feed_urls(cat, city)
            raw_items = []
            failed = []
            for url in urls:
                try:
                    raw_items.extend(parse_rss(fetch_text(url)))
                except urllib.error.HTTPError as e:
                    failed.append("HTTP %s" % e.code)
                except Exception as e:
                    failed.append(str(e))
            # Dedup by title+link, keep first (Urdu feed first for local/PK).
            seen = set()
            items = []
            for it in raw_items:
                k = norm_key(it["title"], it["link"])
                if k in seen:
                    continue
                seen.add(k)
                items.append(it)
            items = items[:12]
            if not items:
                if key in _cache and _cache[key][0] > now - 3600:
                    payload = dict(_cache[key][1])
                    payload["stale"] = True
                    payload["cached"] = True
                    return self._send(200, payload)
                return self._send(
                    502,
                    {
                        "detail": "News feed nahi mil raha"
                        + (" (" + ", ".join(failed) + ")" if failed else "")
                        + " - thodi der baad dobara try karo.",
                    },
                )
            translated = translate_batch(items, debug)
            out_items = []
            for i, it in enumerate(items):
                title_ur, summary_ur = translated.get(i, (None, None))
                if is_urdu(it["title"]):
                    title_ur = it["title"]
                    summary_ur = it["desc"] if is_urdu(it["desc"]) else (summary_ur or "")
                    was_translated = True
                elif title_ur:
                    was_translated = True
                else:
                    title_ur = it["title"]
                    summary_ur = it["desc"]
                    was_translated = False
                out_items.append(
                    {
                        "id": "n%d" % i,
                        "title_ur": title_ur,
                        "title_en": it["title"],
                        "summary_ur": summary_ur,
                        "source": it["source"],
                        "time": it["time"],
                        "link": it["link"],
                        "category": cat,
                        "region": region,
                        "type": "news",
                        "translated": was_translated,
                    }
                )
            payload = {
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "stale": False,
                "cached": False,
                "items": out_items,
            }
            if debug is not None:
                payload["debug"] = debug
            _cache[key] = (now + CACHE_TTL, payload)
            return self._send(200, payload)
        except Exception as e:  # noqa: BLE001 - surface as JSON, never crash
            return self._send(500, {"detail": str(e)})
