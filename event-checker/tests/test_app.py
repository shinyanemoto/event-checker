import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as event_checker_app


class EventCheckerConfigStateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sources_file = Path(self.temp_dir.name) / "sources.json"
        self.settings_file = Path(self.temp_dir.name) / "settings.json"
        self.state_file = Path(self.temp_dir.name) / "source_state.json"

        self.original_sources_file = event_checker_app.CONFIG_SOURCES_FILE
        self.original_settings_file = event_checker_app.SETTINGS_FILE
        self.original_state_file = event_checker_app.STATE_FILE

        event_checker_app.CONFIG_SOURCES_FILE = self.sources_file
        event_checker_app.SETTINGS_FILE = self.settings_file
        event_checker_app.STATE_FILE = self.state_file
        event_checker_app.app.config["TESTING"] = True

        event_checker_app.save_source_configs(
            [
                {
                    "name": "初期データ",
                    "url": "https://example.com/events",
                }
            ]
        )
        event_checker_app.save_settings({"priority_keywords": ["募集", "先着"]})
        self.client = event_checker_app.app.test_client()

    def tearDown(self):
        event_checker_app.CONFIG_SOURCES_FILE = self.original_sources_file
        event_checker_app.SETTINGS_FILE = self.original_settings_file
        event_checker_app.STATE_FILE = self.original_state_file
        self.temp_dir.cleanup()

    def test_load_sources_merges_git_config_and_runtime_state(self):
        event_checker_app.save_source_state(
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

        sources = event_checker_app.load_sources()

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["name"], "初期データ")
        self.assertEqual(sources[0]["last_hash"], "abc123")
        self.assertEqual(sources[0]["last_diff"], ["募集開始"])

    def test_check_updates_runtime_state_file(self):
        with mock.patch.object(event_checker_app, "fetch_page_text", return_value="開催情報があります"):
            response = self.client.post("/check")

        self.assertEqual(response.status_code, 302)
        self.assertIn("status=checked", response.headers["Location"])

        saved_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved_state[0]["url"], "https://example.com/events")
        self.assertTrue(saved_state[0]["last_checked"])
        self.assertTrue(saved_state[0]["last_hash"])

    def test_index_shows_git_managed_config_note(self):
        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Git管理のJSONファイル", body)
        self.assertIn("sources.json", body)
        self.assertIn("settings.json", body)


if __name__ == "__main__":
    unittest.main()
