import sqlite3

DB_PATH = "audit_log.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS content (
                content_id        TEXT    PRIMARY KEY,
                creator_id        TEXT    NOT NULL,
                text              TEXT    NOT NULL,
                timestamp         TEXT    NOT NULL,
                attribution       TEXT,
                label_text        TEXT,
                llm_score         REAL,
                stylometric_score REAL,
                confidence        REAL,
                status            TEXT    NOT NULL DEFAULT 'classified',
                appeal_reason     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id        TEXT    NOT NULL REFERENCES content(content_id),
                creator_id        TEXT,
                timestamp         TEXT    NOT NULL,
                event_type        TEXT    NOT NULL,
                attribution       TEXT,
                confidence        REAL,
                llm_score         REAL,
                stylometric_score REAL,
                label_text        TEXT,
                status            TEXT,
                appeal_reason     TEXT
            )
        """)


def get_log(limit: int = 3) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_submission(content_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM content WHERE content_id = ?", (content_id,)
        ).fetchone()
    return dict(row) if row else None


def log_submission(
    content_id: str,
    creator_id: str,
    text: str,
    timestamp: str,
    attribution: str,
    label_text: str,
    llm_score: float,
    stylometric_score: float,
    confidence: float,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO content
                (content_id, creator_id, text, timestamp, attribution, label_text,
                 llm_score, stylometric_score, confidence, status)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, 'classified')
            """,
            (content_id, creator_id, text, timestamp, attribution, label_text,
             llm_score, stylometric_score, confidence),
        )
        conn.execute(
            """
            INSERT INTO audit_log
                (content_id, creator_id, timestamp, event_type, attribution, confidence,
                 llm_score, stylometric_score, label_text, status)
            VALUES
                (?, ?, ?, 'submission', ?, ?, ?, ?, ?, 'classified')
            """,
            (content_id, creator_id, timestamp, attribution, confidence,
             llm_score, stylometric_score, label_text),
        )


def process_appeal(
    content_id: str,
    appeal_reason: str,
    timestamp: str,
    creator_id: str,
    attribution: str,
    label_text: str,
    llm_score: float,
    stylometric_score: float,
    confidence: float,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE content
            SET status = 'Under Review', appeal_reason = ?
            WHERE content_id = ?
            """,
            (appeal_reason, content_id),
        )
        conn.execute(
            """
            INSERT INTO audit_log
                (content_id, creator_id, timestamp, event_type, attribution, confidence,
                 llm_score, stylometric_score, label_text, status, appeal_reason)
            VALUES
                (?, ?, ?, 'appeal', ?, ?, ?, ?, ?, 'Under Review', ?)
            """,
            (content_id, creator_id, timestamp, attribution, confidence,
             llm_score, stylometric_score, label_text, appeal_reason),
        )
