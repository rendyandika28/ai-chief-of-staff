"""Goal → Projects → Tasks persistence. Read-only for now — writes via PlanningAgent (future)."""

from datetime import datetime
from typing import Optional
from app.lib.database import Database


class GoalManager:
    def __init__(self, db_path: str = "memory/goals.db"):
        self._db = Database(db_path)
        self._init()

    def _init(self):
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL
            )
        """)
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                sort_order INTEGER DEFAULT 0
            )
        """)
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                tool_action TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                sort_order INTEGER DEFAULT 0
            )
        """)

    def create_goal(self, user_id: str, title: str) -> int:
        now = datetime.now().isoformat()
        return self._db.insert(
            "INSERT INTO goals (user_id, title, status, created_at) VALUES (?, ?, 'active', ?)",
            (user_id, title, now),
        )

    def create_project(self, goal_id: int, title: str) -> int:
        return self._db.insert(
            "INSERT INTO projects (goal_id, title, status) VALUES (?, ?, 'pending')",
            (goal_id, title),
        )

    def create_task(self, project_id: int, title: str, description: str = "",
                    tool_action: str = "") -> int:
        return self._db.insert(
            "INSERT INTO tasks (project_id, title, description, tool_action, status) VALUES (?, ?, ?, ?, 'pending')",
            (project_id, title, description, tool_action),
        )

    def list_goals(self, user_id: str, status: str = "active") -> list:
        rows = self._db.fetch(
            "SELECT id, title, status, created_at FROM goals WHERE user_id = ? AND status = ? ORDER BY id DESC",
            (user_id, status),
        )
        return [{"id": r[0], "title": r[1], "status": r[2], "created_at": r[3]} for r in rows]

    def list_projects(self, goal_id: int) -> list:
        rows = self._db.fetch(
            "SELECT id, title, status FROM projects WHERE goal_id = ? ORDER BY id",
            (goal_id,),
        )
        return [{"id": r[0], "title": r[1], "status": r[2]} for r in rows]

    def list_tasks(self, project_id: int) -> list:
        rows = self._db.fetch(
            "SELECT id, title, description, tool_action, status FROM tasks WHERE project_id = ? ORDER BY id",
            (project_id,),
        )
        return [{"id": r[0], "title": r[1], "description": r[2], "tool_action": r[3], "status": r[4]} for r in rows]

    def next_task(self, goal_id: int) -> Optional[dict]:
        projects = self._db.fetch(
            "SELECT id FROM projects WHERE goal_id = ? AND status IN ('pending', 'active') ORDER BY id LIMIT 1",
            (goal_id,),
        )
        if not projects:
            return None
        rows = self._db.fetch(
            "SELECT id, title, description, tool_action FROM tasks WHERE project_id = ? AND status = 'pending' ORDER BY id LIMIT 1",
            (projects[0][0],),
        )
        if not rows:
            return None
        return {"id": rows[0][0], "title": rows[0][1], "description": rows[0][2], "tool_action": rows[0][3]}

    def summary(self, user_id: str) -> str:
        goals = self.list_goals(user_id)
        if not goals:
            return "Belum ada goal aktif."
        lines = []
        for g in goals:
            lines.append(f"[G{g['id']}] {g['title']} ({g['status']})")
            for p in self.list_projects(g["id"]):
                icon = {"pending": "○", "in_progress": "●", "done": "✓"}.get(p["status"], "?")
                lines.append(f"  {icon} {p['title']}")
                for t in self.list_tasks(p["id"]):
                    icon_t = {"pending": "○", "in_progress": "●", "done": "✓"}.get(t["status"], "?")
                    lines.append(f"    {icon_t} {t['title']}")
        return "\n".join(lines)
