"""Локальна історія перевірок для демо."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import zlib


ACTIONS_BY_ROUTE = {
    "STANDARD": ("next", "request", "escalate"),
    "MANUAL_REVIEW": ("request", "escalate"),
    "ESCALATE": ("escalate",),
}


ENTRY_COLUMNS = """
    id, created_at, check_date, company, code, route, route_label,
    mode_label, rule_version, decision, comment, decided_at, restored_at
"""


def summary_values(report):
    names = report["fields"]["name"]["findings"]
    company = names[0]["value"] if names else ""
    code = " / ".join(finding["value"] for finding in report["fields"]["code"]["findings"])
    return company, code, report["check_date"], report["route"], report["mode_label"], report["rule_version"]


def pack_report(report):
    return zlib.compress(json.dumps(report, ensure_ascii=False).encode("utf-8"))


class History:
    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    check_date TEXT NOT NULL,
                    company TEXT NOT NULL,
                    code TEXT NOT NULL,
                    route TEXT NOT NULL,
                    route_label TEXT NOT NULL,
                    mode_label TEXT NOT NULL,
                    rule_version TEXT,
                    decision TEXT,
                    comment TEXT,
                    decided_at TEXT,
                    snapshot BLOB,
                    restored_at TEXT
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(checks)")}
            if "snapshot" not in columns:
                db.execute("ALTER TABLE checks ADD COLUMN snapshot BLOB")
            if "restored_at" not in columns:
                db.execute("ALTER TABLE checks ADD COLUMN restored_at TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, report):
        company, code, check_date, route, mode_label, rule_version = summary_values(report)
        snapshot = pack_report(report)
        with self.connect() as db:
            entry = db.execute("""
                INSERT INTO checks (created_at, check_date, company, code, route, route_label, mode_label, rule_version, snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                check_date, company, code, route, report["route_label"], mode_label, rule_version, snapshot,
            ))
            return entry.lastrowid

    def entries(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"SELECT {ENTRY_COLUMNS} FROM checks ORDER BY id DESC")]

    def details(self, entry_id):
        with self.connect() as db:
            row = db.execute(f"SELECT {ENTRY_COLUMNS}, snapshot FROM checks WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise LookupError("Перевірку не знайдено в історії.")
        entry = dict(row)
        snapshot = entry.pop("snapshot")
        report = json.loads(zlib.decompress(snapshot)) if snapshot is not None else None
        if report is not None:
            report["history_id"] = entry["id"]
            report["permitted_actions"] = ACTIONS_BY_ROUTE[entry["route"]]
        return entry, report

    def restore_report(self, entry_id, report):
        restored_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connect() as db:
            updated = db.execute("""
                UPDATE checks SET snapshot = ?, restored_at = ?
                WHERE id = ? AND snapshot IS NULL AND company = ? AND code = ?
                AND check_date = ? AND route = ? AND mode_label = ? AND rule_version IS ?
            """, (pack_report(report), restored_at, entry_id, *summary_values(report)))
            return updated.rowcount == 1

    def save_decision(self, entry_id, action, comment):
        if type(entry_id) is not int or entry_id < 1 or not isinstance(action, str) or not isinstance(comment, str) or len(comment) > 240:
            raise ValueError("Некоректні дані дії працівника.")
        with self.connect() as db:
            entry = db.execute("SELECT route FROM checks WHERE id = ?", (entry_id,)).fetchone()
            if entry is None:
                raise LookupError("Перевірку не знайдено в історії.")
            if action not in ACTIONS_BY_ROUTE[entry["route"]]:
                raise ValueError("Ця дія недоступна для маршруту перевірки.")
            db.execute(
                "UPDATE checks SET decision = ?, comment = ?, decided_at = ? WHERE id = ?",
                (action, comment.strip(), datetime.now(timezone.utc).isoformat(timespec="seconds"), entry_id),
            )
