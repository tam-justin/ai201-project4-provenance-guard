import sqlite3

DB_PATH = "audit_log.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                content_id  TEXT    PRIMARY KEY,
                creator_id  TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL,
                attribution TEXT,
                confidence  REAL,
                llm_score   REAL    NOT NULL,
                status      TEXT    NOT NULL
            )
        """)


def get_log(limit: int = 3) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def log_submission(
    content_id: str,
    creator_id: str,
    timestamp: str,
    llm_score: float,
    attribution: str | None = None,
    confidence: float | None = None,
    status: str = "classified",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit_log
                (content_id, creator_id, timestamp, attribution, confidence, llm_score, status)
            VALUES
                (?, ?, ?, ?, ?, ?, ?)
            """,
            (content_id, creator_id, timestamp, attribution, confidence, llm_score, status),
        )
