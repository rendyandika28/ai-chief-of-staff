import json
import os
import subprocess
import urllib.request

from app.tools.base import Tool

CCTV_API = "https://cctv.jogjakota.go.id/home/getdata"
CCTV_MAP = "https://cctv.jogjakota.go.id"

CAMERA_CACHE = None


def _fetch_cameras():
    global CAMERA_CACHE
    if CAMERA_CACHE is not None:
        return CAMERA_CACHE

    req = urllib.request.Request(
        CCTV_API,
        headers={
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        CAMERA_CACHE = json.loads(resp.read().decode("utf-8"))
    return CAMERA_CACHE


class CctvTool(Tool):
    name = "cctv"
    description = (
        "CCTV Jogja network (cctv.jogjakota.go.id). 154 cameras across Yogyakarta. "
        "Commands: list:<area>, view:<camera_id|area_name>, info:<camera_id>. "
        "'list' searches by kecamatan, kelurahan, or keyword in title. "
        "'view' captures a 10-second video clip from the camera."
    )

    def __init__(self, browser_tool=None):
        self._browser = browser_tool

    def run(self, input: str = "", user_id: str = "") -> str:
        parts = input.strip().split(":", 1)
        cmd = parts[0].strip().lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        try:
            cameras = _fetch_cameras()
        except Exception as e:
            return f"Error fetching CCTV data: {e}"

        if cmd == "list":
            return self._list_cameras(cameras, arg)

        if cmd == "view":
            return self._view_camera(cameras, arg)

        if cmd == "info":
            return self._camera_info(cameras, arg)

        return "Commands: list:<area>, view:<id|area>, info:<id>"

    def _match(self, cameras, query):
        q = query.lower()
        active = [c for c in cameras if c["cctv_status"] != "2"]

        # ponytail: simple keyword match across all location fields
        def score(c):
            text = (
                f"{c['cctv_title']} {c['kecamatan_nama']} "
                f"{c['kelurahan_nama']} {c['kampung_nama']}"
            ).lower()
            return sum(1 for w in q.split() if w in text)

        scored = [(score(c), c) for c in active]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for s, c in scored if s > 0]

    def _list_cameras(self, cameras, query):
        matched = self._match(cameras, query) if query else cameras[:10]

        if not matched:
            return f"Tidak ada CCTV ditemukan untuk: {query}"

        lines = []
        for c in matched[:15]:
            loc = f"{c['kecamatan_nama']} > {c['kelurahan_nama']}"
            name = c["cctv_title"]
            status = {0: "aktif", 1: "private", 2: "rusak"}.get(
                int(c["cctv_status"]), "?"
            )
            lines.append(
                f"  [{c['cctv_id']}] {name} — {loc} ({status})"
            )

        return "\n".join(lines)

    def _camera_info(self, cameras, cam_id):
        cam = None
        for c in cameras:
            if c["cctv_id"] == cam_id:
                cam = c
                break
        if not cam:
            return f"Camera {cam_id} not found"

        return (
            f"Camera #{cam['cctv_id']}: {cam['cctv_title']}\n"
            f"  Lokasi: {cam['kecamatan_nama']} > {cam['kelurahan_nama']}"
            + (f" > {cam['kampung_nama']}" if cam['kampung_nama'] else "") + "\n"
            f"  Koordinat: {cam['cctv_latitude']}, {cam['cctv_longitude']}\n"
            f"  Stream: {cam['cctv_link']}\n"
            f"  Status: {('aktif', 'private', 'rusak')[int(cam['cctv_status'])]}"
        )

    def _view_camera(self, cameras, arg):
        cam = None
        for c in cameras:
            if c["cctv_id"] == arg:
                cam = c
                break
        if cam is None:
            matched = self._match(cameras, arg)
            if matched:
                cam = matched[0]
        if cam is None:
            return f"Camera tidak ditemukan: {arg}"

        name = cam["cctv_title"]
        stream_url = cam["cctv_link"]
        area = f"{cam['kecamatan_nama']} > {cam['kelurahan_nama']}"

        lines = [f"Camera: {name}", f"Area: {area}"]

        # Try ffmpeg first for 5-second video clip
        video_path = self._capture_video(stream_url, name)
        if video_path:
            lines.append(f"[VIDEO:{video_path}]")

        # Fallback: single screenshot if no ffmpeg
        if not video_path and self._browser:
            img = self._capture_screenshot(stream_url, name)
            if img:
                lines.append(f"[IMAGE:{img}]")

        return "\n".join(lines)

    def _capture_video(self, stream_url: str, name: str) -> str:
        slug = name.lower().replace(" ", "_")[:20]
        out = f"memory/cctv_{slug}.mp4"
        os.makedirs("memory", exist_ok=True)

        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", stream_url, "-t", "10", "-c", "copy",
                 "-loglevel", "error", out],
                timeout=30, capture_output=True,
            )
            if os.path.exists(out) and os.path.getsize(out) > 1000:
                return out
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass
        return ""

    def _capture_screenshot(self, stream_url: str, name: str) -> str:
        try:
            html = (
                "<html><head>"
                "<script src='https://cdn.jsdelivr.net/npm/hls.js@1'></script>"
                "</head><body style='margin:0;background:#000'>"
                "<video id='v' autoplay muted playsinline style='width:100vw;height:100vh'></video>"
                "<script>"
                "const v=document.getElementById('v');"
                f"if(Hls.isSupported()){{const h=new Hls();h.loadSource('{stream_url}');h.attachMedia(v);h.on(Hls.Events.MANIFEST_PARSED,()=>v.play());}}"
                f"else if(v.canPlayType('application/vnd.apple.mpegurl')){{v.src='{stream_url}';v.play();}}"
                "</script></body></html>"
            ).replace("Hls", "Hls")  # no-op, just keeping hls.js ref

            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
                f.write(html)
                tmp_path = f.name

            self._browser.run(f"navigate:file://{tmp_path}")
            import time; time.sleep(3)
            result = self._browser.run("screenshot")
            if "Screenshot saved:" in result:
                return result.split("Screenshot saved:")[1].strip()
        except Exception:
            pass
        return ""
