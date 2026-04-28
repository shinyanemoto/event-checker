import io
import json
import tempfile
import unittest
from pathlib import Path

import app as event_checker_app


class EventCheckerImportExportTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sources_file = Path(self.temp_dir.name) / "sources.json"
        self.settings_file = Path(self.temp_dir.name) / "settings.json"

        event_checker_app.SOURCES_FILE = self.sources_file
        event_checker_app.SETTINGS_FILE = self.settings_file
        event_checker_app.app.config["TESTING"] = True

        event_checker_app.save_sources(
            [
                {
                    "name": "初期データ",
                    "url": "https://example.com/events",
                    "last_checked": "2026-04-28T00:00:00+00:00",
                    "last_hash": "abc123",
                    "last_diff": ["募集開始"],
                    "last_text": "募集開始",
                }
            ]
        )
        event_checker_app.save_settings({"priority_keywords": ["募集", "先着"]})
        self.client = event_checker_app.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_export_returns_backup_json(self):
        response = self.client.get("/export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn("attachment;", response.headers["Content-Disposition"])

        payload = response.get_json()
        self.assertEqual(payload["format"], "event-checker-export")
        self.assertEqual(payload["settings"]["priority_keywords"], ["募集", "先着"])
        self.assertEqual(payload["sources"][0]["name"], "初期データ")

    def test_import_replaces_sources_and_settings(self):
        import_payload = {
            "format": "event-checker-export",
            "version": 1,
            "sources": [
                {
                    "name": "差し替え後",
                    "url": "https://example.com/new",
                    "last_checked": "",
                    "last_hash": "",
                    "last_diff": [],
                    "last_text": "",
                }
            ],
            "settings": {
                "priority_keywords": ["抽選", "開催"],
            },
        }

        response = self.client.post(
            "/import",
            data={
                "import_file": (
                    io.BytesIO(json.dumps(import_payload, ensure_ascii=False).encode("utf-8")),
                    "backup.json",
                )
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("status=imported", response.headers["Location"])
        self.assertEqual(event_checker_app.load_sources()[0]["name"], "差し替え後")
        self.assertEqual(event_checker_app.load_settings()["priority_keywords"], ["抽選", "開催"])

    def test_import_rejects_invalid_json(self):
        response = self.client.post(
            "/import",
            data={
                "import_file": (
                    io.BytesIO(b"{not-json}"),
                    "broken.json",
                )
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("status=import_invalid", response.headers["Location"])
        self.assertEqual(event_checker_app.load_sources()[0]["name"], "初期データ")


if __name__ == "__main__":
    unittest.main()
