import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as event_checker_app


class EventCheckerImportExportTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sources_file = Path(self.temp_dir.name) / "sources.json"
        self.settings_file = Path(self.temp_dir.name) / "settings.json"
        self.screenshot_dir = Path(self.temp_dir.name) / "static" / "screenshots"

        self.original_sources_file = event_checker_app.SOURCES_FILE
        self.original_settings_file = event_checker_app.SETTINGS_FILE
        self.original_screenshot_dir = event_checker_app.SCREENSHOT_DIR

        event_checker_app.SOURCES_FILE = self.sources_file
        event_checker_app.SETTINGS_FILE = self.settings_file
        event_checker_app.SCREENSHOT_DIR = self.screenshot_dir
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
                    "screenshot_updated_at": "",
                    "screenshot_error": "",
                }
            ]
        )
        event_checker_app.save_settings({"priority_keywords": ["募集", "先着"]})
        self.client = event_checker_app.app.test_client()

    def tearDown(self):
        event_checker_app.SOURCES_FILE = self.original_sources_file
        event_checker_app.SETTINGS_FILE = self.original_settings_file
        event_checker_app.SCREENSHOT_DIR = self.original_screenshot_dir
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

    def test_check_updates_thumbnail_metadata_and_saves_file(self):
        def fake_capture(url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"fake-image")

        with (
            mock.patch.object(event_checker_app, "fetch_page_text", return_value="開催情報があります"),
            mock.patch.object(event_checker_app, "capture_page_screenshot", side_effect=fake_capture),
        ):
            response = self.client.post("/check")

        self.assertEqual(response.status_code, 302)
        self.assertIn("status=checked", response.headers["Location"])
        source = event_checker_app.load_sources()[0]
        self.assertTrue(source["screenshot_updated_at"])
        self.assertEqual(source["screenshot_error"], "")
        self.assertTrue(event_checker_app.get_screenshot_file_path(source["url"]).exists())

    def test_index_shows_thumbnail_when_image_exists(self):
        screenshot_file = event_checker_app.get_screenshot_file_path("https://example.com/events")
        screenshot_file.parent.mkdir(parents=True, exist_ok=True)
        screenshot_file.write_bytes(b"fake-image")

        sources = event_checker_app.load_sources()
        sources[0]["screenshot_updated_at"] = "2026-04-28T01:00:00+00:00"
        event_checker_app.save_sources(sources)

        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("最新サムネイル", body)
        self.assertIn("/static/screenshots/", body)
        self.assertIn("サムネイル更新", body)


if __name__ == "__main__":
    unittest.main()
