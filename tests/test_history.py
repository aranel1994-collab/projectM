"""Історія після відкриття бази повторно та валідація дій."""

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from mono_check.analysis import analyze
from mono_check.history import History


FIXTURE = Path(__file__).resolve().parent.parent / "output" / "pdf" / "standard.pdf"


class HistoryTest(unittest.TestCase):
    def test_checks_and_decision_persist_in_the_correct_entry(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "history.sqlite3"
            history = History(path)
            pdf = FIXTURE.read_bytes()
            report = analyze(pdf, "replay", "00073142", "2026-11-24")
            first = history.record(report)
            second = history.record(report)
            history.save_decision(first, "request", "Уточнити дані заявки")

            latest, original = History(path).entries()
            self.assertEqual((latest["id"], original["id"]), (second, first))
            self.assertIsNone(latest["decision"])
            self.assertEqual(original["code"], "00073142")
            self.assertEqual(original["decision"], "request")
            self.assertEqual(original["comment"], "Уточнити дані заявки")
            self.assertTrue(original["decided_at"])
            for key in ("snapshot", "pages", "fields", "results", "pdf"):
                self.assertNotIn(key, original)
            reopened_entry, reopened_report = History(path).details(first)
            self.assertEqual(reopened_entry["decision"], "request")
            self.assertEqual(reopened_report["fields"], report["fields"])
            self.assertEqual(reopened_report["pages"], report["pages"])
            self.assertEqual(reopened_report["history_id"], first)
            self.assertIsNone(reopened_entry["restored_at"])

    def test_decision_validates_route_and_record(self):
        with TemporaryDirectory() as directory:
            history = History(Path(directory) / "history.sqlite3")
            report = analyze(FIXTURE.read_bytes(), "replay", "00170934", "2026-11-24", include_pages=False)
            entry = history.record(report)
            with self.assertRaisesRegex(ValueError, "недоступна"):
                history.save_decision(entry, "next", "")
            with self.assertRaises(LookupError):
                history.save_decision(entry + 1, "request", "")
            self.assertIsNone(history.entries()[0]["decision"])


    def test_legacy_summary_restores_without_changing_existing_decision(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""
                    CREATE TABLE checks (
                        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, check_date TEXT NOT NULL,
                        company TEXT NOT NULL, code TEXT NOT NULL, route TEXT NOT NULL, route_label TEXT NOT NULL,
                        mode_label TEXT NOT NULL, rule_version TEXT, decision TEXT, comment TEXT, decided_at TEXT
                    )
                """)
                pdf = FIXTURE.read_bytes()
                report = analyze(pdf, "replay", "00073142", "2026-11-24")
                company = report["fields"]["name"]["findings"][0]["value"]
                db.execute(
                    "INSERT INTO checks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (1, "2026-11-25T09:00:00+00:00", report["check_date"], company, "00073142",
                     report["route"], report["route_label"], report["mode_label"], report["rule_version"],
                     "request", "Уточнити дані", "2026-11-25T09:01:00+00:00"),
                )

            history = History(path)
            self.assertIsNone(history.details(1)[1])
            self.assertFalse(history.restore_report(1, {**report, "check_date": "2026-11-25"}))
            self.assertTrue(history.restore_report(1, report))
            self.assertFalse(history.restore_report(1, report))
            entry, saved = History(path).details(1)
            self.assertEqual(len(history.entries()), 1)
            self.assertEqual(entry["created_at"], "2026-11-25T09:00:00+00:00")
            self.assertEqual(entry["decision"], "request")
            self.assertEqual(entry["comment"], "Уточнити дані")
            self.assertTrue(entry["restored_at"])
            self.assertEqual(saved["pages"], report["pages"])
