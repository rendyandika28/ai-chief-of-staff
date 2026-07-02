"""Home CCTV tool — captures video from any RTSP/ONVIF camera via ffmpeg."""

import os
import subprocess
from app.tools.base import Tool


class HomeCctvTool(Tool):
    name = "cctv_home"
    description = (
        "Home CCTV via RTSP. Input: rtsp:<url> (e.g. rtsp://admin:pass@192.168.1.10:554/live/ch0). "
        "Captures 10s video clip via ffmpeg."
    )

    def run(self, input: str = "", user_id: str = "") -> str:
        parts = input.strip().split(":", 1)
        if len(parts) < 2 or parts[0] != "rtsp":
            return (
                "Error: format is rtsp:<url>\n"
                "Contoh: rtsp://admin:password@192.168.1.10:554/live/ch0"
            )

        rtsp_url = parts[1].strip()
        if not rtsp_url.startswith("rtsp://"):
            rtsp_url = "rtsp://" + rtsp_url

        out = "memory/cctv_home.mp4"
        os.makedirs("memory", exist_ok=True)

        try:
            subprocess.run(
                ["ffmpeg", "-y", "-rtsp_transport", "tcp",
                 "-i", rtsp_url, "-t", "10", "-c", "copy",
                 "-loglevel", "error", out],
                timeout=20, capture_output=True, check=True,
            )
            if os.path.exists(out) and os.path.getsize(out) > 5000:
                return f"Camera: Home CCTV\n[VIDEO:{os.path.abspath(out)}]"
            return "Error: video file empty. Cek RTSP URL atau kamera lagi offline."
        except subprocess.CalledProcessError as e:
            return f"Error: ffmpeg gagal. Cek RTSP URL (port, user, password).\nDetail: {e.stderr.decode()[-200:] if e.stderr else 'unknown'}"
        except subprocess.TimeoutExpired:
            return "Error: timeout. Kamera mungkin offline atau RTSP URL salah."
        except FileNotFoundError:
            return "Error: ffmpeg tidak terinstall. Jalankan: apt install -y ffmpeg"
