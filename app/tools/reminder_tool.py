from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


MONTHS = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]
DAYS = {
    "senin": "Senin", "monday": "Senin",
    "selasa": "Selasa", "tuesday": "Selasa",
    "rabu": "Rabu", "wednesday": "Rabu",
    "kamis": "Kamis", "thursday": "Kamis",
    "jumat": "Jumat", "friday": "Jumat",
    "sabtu": "Sabtu", "saturday": "Sabtu",
    "minggu": "Minggu", "sunday": "Minggu",
}


class ReminderTool:
    name = "reminder"
    description = (
        "Set reminders. Formats: "
        "delay:<seconds>:<msg>, "
        "at:<ISO-datetime>:<msg>, "
        "daily:<HH:MM>:<msg>, "
        "weekly:<day>:<HH:MM>:<msg> (day: senin/monday/selasa, etc)"
    )

    def __init__(self, scheduler):
        self._scheduler = scheduler

    def _parse(self, input_str: str) -> tuple:
        task_type = input_str.split(":")[0].strip().lower()

        if task_type in ("delay", "every"):
            parts = input_str.split(":", 2)
            if len(parts) < 3:
                raise ValueError(f"format: {task_type}:<seconds>:<message>")
            return task_type, [parts[1].strip()], parts[2].strip()

        if task_type == "at":
            prefix = len("at:")
            iso = input_str[prefix:prefix + 19]
            msg = input_str[prefix + 20:]
            if not iso or len(iso) < 16:
                raise ValueError("format: at:<YYYY-MM-DDTHH:MM:SS>:<message>")
            return "at", [iso], msg.strip()

        if task_type == "daily":
            prefix = len("daily:")
            time_str = input_str[prefix:prefix + 5]
            msg = input_str[prefix + 6:]
            if ":" not in time_str or len(time_str) < 4:
                raise ValueError("format: daily:<HH:MM>:<message>")
            return "daily", [time_str], msg.strip()

        if task_type == "weekly":
            rest = input_str[len("weekly:"):]
            first = rest.find(":")
            if first == -1:
                raise ValueError("format: weekly:<day>:<HH:MM>:<message>")
            day = rest[:first]
            time_str = rest[first + 1:first + 6]
            msg = rest[first + 7:]
            if ":" not in time_str:
                raise ValueError("format: weekly:<day>:<HH:MM>:<message>")
            return "weekly", [day.strip(), time_str.strip()], msg.strip()

        raise ValueError(f"unknown type '{task_type}'. Use delay, every, at, daily, weekly.")

    def _fmt_time(self, seconds: int) -> str:
        if seconds < 60:
            return f"{seconds} detik"
        if seconds < 3600:
            return f"{seconds // 60} menit"
        if seconds < 86400:
            h, m = divmod(seconds, 3600)
            return f"{h} jam" + (f" {m} menit" if m else "")
        d = seconds // 86400
        return f"{d} hari"

    def _fmt_iso(self, iso: str) -> str:
        try:
            dt = datetime.fromisoformat(iso)
            return f"{dt.day} {MONTHS[dt.month]} {dt.year}, {dt.strftime('%H:%M')}"
        except ValueError:
            return iso

    def run(self, input: str = "", user_id: str = "") -> str:
        try:
            task_type, params, message = self._parse(input)
        except ValueError as e:
            return f"Error: {e}"

        if not message:
            return "Error: message is required"
        if not user_id:
            return "Error: user_id required"

        if task_type == "delay":
            try:
                seconds = int(params[0])
            except ValueError:
                return "Error: seconds must be a number"
            self._scheduler.add(user_id, message, delay_seconds=seconds)
            return f"Oke, gue ingetin {self._fmt_time(seconds)} lagi: {message}"

        if task_type == "every":
            try:
                seconds = int(params[0])
            except ValueError:
                return "Error: seconds must be a number"
            self._scheduler.add(user_id, message, interval_seconds=seconds)
            return f"Oke, gue ingetin setiap {self._fmt_time(seconds)}: {message}"

        if task_type == "at":
            # ponytail: correct hallucinated dates. If year is wrong (LLM quirk), use today.
            try:
                dt = datetime.fromisoformat(params[0])
                now = datetime.now(WIB)
                if dt.year < now.year:
                    dt = dt.replace(year=now.year)
                    if dt < now:
                        dt = dt.replace(year=now.year + 1)
                    params[0] = dt.isoformat()
            except ValueError:
                pass
            self._scheduler.add(user_id, message, run_at=params[0])
            return f"Oke, gue ingetin tanggal {self._fmt_iso(params[0])}: {message}"

        if task_type == "daily":
            run_at, interval = self._scheduler.calc_daily(params[0])
            self._scheduler.add(user_id, message, run_at=run_at, interval_seconds=interval)
            return f"Oke, gue ingetin setiap hari jam {params[0]}: {message}"

        if task_type == "weekly":
            run_at, interval = self._scheduler.calc_weekly(params[0], params[1])
            day_name = DAYS.get(params[0].lower(), params[0])
            self._scheduler.add(user_id, message, run_at=run_at, interval_seconds=interval)
            return f"Oke, gue ingetin setiap {day_name} jam {params[1]}: {message}"

        return f"Error: unknown type '{task_type}'"
