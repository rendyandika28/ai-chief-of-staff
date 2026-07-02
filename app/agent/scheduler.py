import threading
import time
from datetime import datetime, timedelta

from app.lib.database import Database

DAYS = {
    "senin": 0, "monday": 0,
    "selasa": 1, "tuesday": 1,
    "rabu": 2, "wednesday": 2,
    "kamis": 3, "thursday": 3,
    "jumat": 4, "friday": 4,
    "sabtu": 5, "saturday": 5,
    "minggu": 6, "sunday": 6,
}


class Scheduler:
    def __init__(self, db_path: str = "memory/scheduler.db", on_notify=None):
        self._db = Database(db_path)
        self._on_notify = on_notify
        self._running = False
        self._thread = None
        self._init_db()

    def _init_db(self):
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                run_at TEXT NOT NULL,
                interval_seconds INTEGER,
                status TEXT DEFAULT 'pending'
            )
        """)

    def add(self, user_id: str, message: str, delay_seconds: int = 0,
            run_at: str = "", interval_seconds: int = 0):
        if not run_at:
            run_at = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat()
        interval = interval_seconds if interval_seconds > 0 else None
        self._db.commit_sql(
            "INSERT INTO tasks (user_id, message, run_at, interval_seconds) VALUES (?, ?, ?, ?)",
            (user_id, message, run_at, interval),
        )

    @staticmethod
    def calc_at(iso: str) -> str:
        return iso

    @staticmethod
    def calc_daily(time_str: str) -> tuple:
        """Returns (run_at_iso, interval_seconds) for daily at HH:MM."""
        h, m = map(int, time_str.split(":"))
        now = datetime.now()
        run_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if run_at <= now:
            run_at += timedelta(days=1)
        return run_at.isoformat(), 86400

    @staticmethod
    def calc_weekly(day: str, time_str: str) -> tuple:
        """Returns (run_at_iso, interval_seconds) for weekly on DAY at HH:MM."""
        h, m = map(int, time_str.split(":"))
        target_dow = DAYS.get(day.lower())
        if target_dow is None:
            raise ValueError(f"Unknown day: {day}")

        now = datetime.now()
        run_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        days_ahead = target_dow - run_at.weekday()
        if days_ahead < 0 or (days_ahead == 0 and run_at <= now):
            days_ahead += 7
        run_at += timedelta(days=days_ahead)
        return run_at.isoformat(), 604800

    def _get_due(self):
        now = datetime.now().isoformat()
        return self._db.fetch(
            "SELECT id, user_id, message, interval_seconds FROM tasks WHERE status='pending' AND run_at <= ?",
            (now,),
        )

    def _mark_done(self, task_id: int):
        self._db.commit_sql("UPDATE tasks SET status='done' WHERE id=?", (task_id,))

    def _reschedule(self, task_id: int, interval_seconds: int):
        run_at = (datetime.now() + timedelta(seconds=interval_seconds)).isoformat()
        self._db.commit_sql("UPDATE tasks SET run_at=? WHERE id=?", (run_at, task_id))

    def _loop(self):
        while self._running:
            try:
                for task in self._get_due():
                    task_id, user_id, message, interval = task
                    if self._on_notify:
                        self._on_notify(user_id, message)
                    if interval:
                        self._reschedule(task_id, interval)
                    else:
                        self._mark_done(task_id)
            except Exception:
                pass
            time.sleep(1)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
