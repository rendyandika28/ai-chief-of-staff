from app.tools.base import Tool


class TrafficTool(Tool):
    name = "traffic"
    description = (
        "Check traffic conditions for a location. Input: city or area name. "
        "Opens Google Maps traffic layer and extracts visible congestion info."
    )

    def __init__(self, browser_tool=None):
        self._browser = browser_tool

    def run(self, input: str = "", user_id: str = "") -> str:
        location = input.strip() or "Yogyakarta"

        if self._browser is None:
            return (
                "Untuk cek lalu lintas, buka Google Maps:\n"
                f"https://www.google.com/maps/search/{location.replace(' ', '+')}\n"
                "Aktifkan layer Traffic (ikon lampu lalu lintas di kanan bawah)."
            )

        try:
            encoded = location.replace(" ", "+")
            url = f"https://www.google.com/maps/search/{encoded}"

            nav = self._browser.run(f"navigate:{url}")
            content = self._browser.run("content")

            lines = content.split("\n")
            traffic_lines = [
                l for l in lines
                if any(w in l.lower() for w in ["lalu lintas", "macet", "lancar", "padat", "traffic", "menit", "km", "jalan"])
            ]

            if traffic_lines:
                return (
                    f"Lalu lintas di {location}:\n" +
                    "\n".join(f"  {l}" for l in traffic_lines[:10])
                )
            return f"Lalu lintas {location}: tidak ada info kemacetan yang terdeteksi.\nLihat langsung: {url}"

        except Exception as e:
            return f"Error cek lalu lintas: {e}\nCek manual: https://www.google.com/maps/search/{location.replace(' ', '+')}"
