from app.lib.database import Database


class Memory:
    def __init__(self):
        self._db = Database("memory/conversations.db")
        self._db.commit_sql("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._db.commit_sql("CREATE INDEX IF NOT EXISTS idx_user_id ON messages(user_id)")

    def get(self, user_id: str, limit: int = 20):
        rows = self._db.fetch(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def add(self, user_id: str, role: str, content: str):
        self._db.commit_sql(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
