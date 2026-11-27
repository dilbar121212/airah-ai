"""Airah AI weather endpoint for Vercel (Python serverless function).

GET /api/weather?city=Karachi
GET /api/weather?lat=24.86&lon=67.01
-> {"city": "...", "country": "...", "current": {...},
    "hourly": [...12h...], "daily": [...7 days...]}

Source: Open-Meteo (free, no API key, no account, no card).
Geocoding + forecast both come from Open-Meteo, so no secret is ever
stored or exposed. Only the Python standard library is used.

WMO weather codes are mapped to English + Urdu condition text here,
so the app and the chat always show the same wording.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO code -> (English, Urdu script, Roman Urdu, emoji)
CONDITIONS = {
    0: ("Clear sky", "صاف آسمان", "Saaf aasmaan", "☀️"),
    1: ("Mainly clear", "زیادہ تر صاف", "Zyada tar saaf", "🌤️"),
    2: ("Partly cloudy", "کچھ بادل", "Kuch badal", "⛅"),
    3: ("Overcast", "بادل چھائے ہوئے", "Badal chhaye hue", "☁️"),
    45: ("Foggy", "دھند", "Dhund", "🌫️"),
    48: ("Foggy", "دھند", "Dhund", "🌫️"),
    51: ("Light drizzle", "ہلکی بونداباندی", "Halki boondabandi", "🌦️"),
    53: ("Drizzle", "بونداباندی", "Boondabandi", "🌦️"),
    55: ("Heavy drizzle", "تیز بونداباندی", "Tez boondabandi", "🌦️"),
    61: ("Light rain", "ہلکی بارش", "Halki barish", "🌧️"),
    63: ("Rain", "بارش", "Barish", "🌧️"),
    65: ("Heavy rain", "تیز بارش", "Tez barish", "⛈️"),
    66: ("Freezing rain", "برف والی بارش", "Barf wali barish", "🌧️"),
    67: ("Freezing rain", "برف والی بارش", "Barf wali barish", "🌧️"),
    71: ("Light snow", "ہلکی برف", "Halki barf", "🌨️"),
    73: ("Snow", "برف", "Barf", "🌨️"),
    75: ("Heavy snow", "تیز برف", "Tez barf", "🌨️"),
    77: ("Snow grains", "برف کے دانے", "Barf ke dane", "🌨️"),
    80: ("Light showers", "ہلکی بوچھاڑ", "Halki bochhar", "🌦️"),
    81: ("Showers", "بوچھاڑ", "Bochhar", "🌦️"),
    82: ("Heavy showers", "تیز بوچھاڑ", "Tez bochhar", "⛈️"),
    85: ("Snow showers", "برف کی بوچھاڑ", "Barf ki bochhar", "🌨️"),
    86: ("Snow showers", "برف کی بوچھاڑ", "Barf ki bochhar", "🌨️"),
    95: ("Thunderstorm", "طوفان / گرج چمک", "Toofan / garaj chamak", "⛈️"),
    96: ("Thunderstorm with hail", "اولوں کے ساتھ طوفان", "Olon ke saath toofan", "⛈️"),
    99: ("Thunderstorm with hail", "اولوں کے ساتھ طوفان", "Olon ke saath toofan", "⛈️"),
}


def condition(code):
    en, ur, roman, emoji = CONDITIONS.get(
        code, ("Unknown", "معلوم نہیں", "Maloom nahi", "🌡️")
    )
    return {"code": code, "en": en, "ur": ur, "roman": roman, "emoji": emoji}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {})

    def do_GET(self):
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            city = (qs.get("city", [""])[0] or "").strip()
            lat = (qs.get("lat", [""])[0] or "").strip()
            lon = (qs.get("lon", [""])[0] or "").strip()
            if not city and not (lat and lon):
                return self._send(
                    400, {"detail": "city or lat+lon is required"}
                )

            place = None
            if city:
                try:
                    geo = fetch_json(
                        GEO_URL
                        + "?"
                        + urllib.parse.urlencode(
                            {
                                "name": city,
                                "count": 1,
                                "language": "en",
                                "format": "json",
                            }
                        )
                    )
                except urllib.error.HTTPError as e:
                    return self._send(502, {"detail": "geocoding failed: " + str(e)})
                results = geo.get("results") or []
                if not results:
                    return self._send(
                        404, {"detail": "City not found: " + city}
                    )
                place = results[0]
                lat, lon = str(place["latitude"]), str(place["longitude"])
                city = place["name"]
                country = place.get("country", "")
            else:
                country = ""

            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "weather_code,wind_speed_10m,precipitation_probability",
                "hourly": "temperature_2m,precipitation_probability,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 7,
            }
            try:
                fc = fetch_json(
                    FORECAST_URL + "?" + urllib.parse.urlencode(params)
                )
            except urllib.error.HTTPError as e:
                return self._send(502, {"detail": "forecast failed: " + str(e)})

            cur = fc.get("current") or {}
            hourly = fc.get("hourly") or {}
            daily = fc.get("daily") or {}
            h_times = hourly.get("time") or []
            h_temp = hourly.get("temperature_2m") or []
            h_rain = hourly.get("precipitation_probability") or []
            h_code = hourly.get("weather_code") or []
            # Next 12 hours from the current hour.
            start = 0
            cur_time = (cur.get("time") or "")[:13]
            for i, t in enumerate(h_times):
                if t[:13] >= cur_time:
                    start = i
                    break
            hours = []
            for i in range(start, min(start + 12, len(h_times))):
                hours.append(
                    {
                        "time": h_times[i],
                        "temp": h_temp[i] if i < len(h_temp) else None,
                        "rain_prob": h_rain[i] if i < len(h_rain) else None,
                        "condition": condition(
                            h_code[i] if i < len(h_code) else -1
                        ),
                    }
                )
            days = []
            d_time = daily.get("time") or []
            d_max = daily.get("temperature_2m_max") or []
            d_min = daily.get("temperature_2m_min") or []
            d_rain = daily.get("precipitation_probability_max") or []
            d_code = daily.get("weather_code") or []
            for i in range(min(7, len(d_time))):
                days.append(
                    {
                        "date": d_time[i],
                        "max": d_max[i] if i < len(d_max) else None,
                        "min": d_min[i] if i < len(d_min) else None,
                        "rain_prob": d_rain[i] if i < len(d_rain) else None,
                        "condition": condition(
                            d_code[i] if i < len(d_code) else -1
                        ),
                    }
                )

            return self._send(
                200,
                {
                    "city": city,
                    "country": country,
                    "timezone": fc.get("timezone", ""),
                    "current": {
                        "temp": cur.get("temperature_2m"),
                        "feels_like": cur.get("apparent_temperature"),
                        "humidity": cur.get("relative_humidity_2m"),
                        "wind": cur.get("wind_speed_10m"),
                        "rain_prob": cur.get("precipitation_probability"),
                        "condition": condition(cur.get("weather_code", -1)),
                    },
                    "hourly": hours,
                    "daily": days,
                },
            )
        except Exception as e:  # noqa: BLE001 - surface as JSON, never crash
            return self._send(500, {"detail": str(e)})
