import json
import time
import urllib.request
import urllib.error


_weather_cache = {}  # city -> (timestamp, result)


class WeatherTool:
    name = "weather"
    description = "Get current weather for a city. Input: city name. Uses Open-Meteo (free, no API key)."

    def run(self, input: str = "") -> str:
        city = input.strip().lower()
        if not city:
            return "Error: city name required"

        # Cache check — 30 min TTL
        if city in _weather_cache:
            ts, result = _weather_cache[city]
            if time.time() - ts < 1800:
                return result

        try:
            lat, lon, name = self._geocode(input.strip())
            weather = self._fetch_weather(lat, lon)
            result = f"Cuaca di {name}: {weather}"
            _weather_cache[city] = (time.time(), result)
            return result
        except Exception as e:
            return f"Error: {e}"

    def _geocode(self, city: str):
        # ponytail: try full string first, then fall back to last comma-part
        candidates = [city]
        if "," in city:
            candidates.append(city.split(",")[-1].strip())

        for c in candidates:
            url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.request.quote(c)}&count=1&language=id"
            req = urllib.request.Request(url, headers={"User-Agent": "AI-Chief-of-Staff/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("results"):
                r = data["results"][0]
                return r["latitude"], r["longitude"], r.get("name", c)

        raise ValueError(f"City not found: {city}")

    def _fetch_weather(self, lat, lon):
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current_weather=true"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&timezone=Asia/Jakarta"
            f"&forecast_days=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Chief-of-Staff/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        cw = data.get("current_weather", {})
        daily = data.get("daily", {})

        temp = cw.get("temperature", "?")
        wind = cw.get("windspeed", "?")
        code = cw.get("weathercode", 0)

        conditions = {
            0: "Cerah", 1: "Cerah", 2: "Berawan", 3: "Mendung",
            45: "Kabut", 48: "Kabut beku",
            51: "Gerimis ringan", 53: "Gerimis", 55: "Gerimis lebat",
            61: "Hujan ringan", 63: "Hujan", 65: "Hujan lebat",
            71: "Salju ringan", 73: "Salju", 75: "Salju lebat",
            80: "Hujan lokal", 81: "Hujan sedang", 82: "Hujan badai",
            95: "Badai petir", 96: "Badai+hujan es", 99: "Badai besar+hujan es",
        }
        cond = conditions.get(code, f"kode {code}")

        parts = [f"{temp}°C, {cond}, angin {wind} km/jam"]

        if daily:
            hi = daily.get("temperature_2m_max", [None])[0]
            lo = daily.get("temperature_2m_min", [None])[0]
            rain = daily.get("precipitation_probability_max", [None])[0]
            if hi is not None and lo is not None:
                parts.append(f"(min {lo}°C / max {hi}°C)")
            if rain is not None:
                parts.append(f"kemungkinan hujan {rain}%")

        return " ".join(parts)
