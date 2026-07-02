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

            self._browser.run(f"navigate:{url}")
            import time; time.sleep(2)
            result = self._browser.run("screenshot")

            img_path = ""
            if "Screenshot saved:" in result:
                img_path = result.split("Screenshot saved:")[1].strip()

            lines = [f"Lalu lintas di {location}:"]
            if img_path:
                lines.append(f"[IMAGE:{img_path}]")
            lines.append(f"Link: {url}")

            return "\n".join(lines)

        except Exception as e:
            return f"Error cek lalu lintas: {e}\nCek manual: https://www.google.com/maps/search/{location.replace(' ', '+')}"
