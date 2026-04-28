import hashlib
import json
import os
from datetime import datetime, timezone
from difflib import ndiff
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
CONFIG_SOURCES_FILE = Path(os.environ.get("SOURCES_PATH", BASE_DIR / "sources.json"))
SETTINGS_FILE = Path(os.environ.get("SETTINGS_PATH", BASE_DIR / "settings.json"))
STATE_FILE = Path(os.environ.get("STATE_PATH", BASE_DIR / "source_state.json"))
REQUEST_TIMEOUT = 15
EXCLUDE_KEYWORDS = [
    "Copyright",
    "All Rights Reserved",
    "ログイン",
    "お問い合わせ",
]
DEFAULT_PRIORITY_KEYWORDS = [
    "募集",
    "開催",
    "イベント",
    "申込",
    "抽選",
]

app = Flask(__name__)


def clean_priority_keywords(priority_keywords):
    cleaned_keywords = []
    for keyword in priority_keywords:
        normalized = str(keyword).strip()
        if normalized and normalized not in cleaned_keywords:
            cleaned_keywords.append(normalized)

    if not cleaned_keywords:
        cleaned_keywords = DEFAULT_PRIORITY_KEYWORDS.copy()

    return cleaned_keywords


def normalize_source_config(source):
    return {
        "name": str(source.get("name", "")).strip(),
        "url": str(source.get("url", "")).strip(),
    }


def normalize_source_state(source):
    return {
        "url": str(source.get("url", "")).strip(),
        "last_checked": str(source.get("last_checked", "")).strip(),
        "last_hash": str(source.get("last_hash", "")).strip(),
        "last_diff": [str(line).strip() for line in source.get("last_diff", []) if str(line).strip()],
        "last_text": str(source.get("last_text", "")),
    }


def load_source_configs():
    if not CONFIG_SOURCES_FILE.exists():
        save_source_configs([])
        return []

    try:
        with CONFIG_SOURCES_FILE.open("r", encoding="utf-8") as file:
            raw_sources = json.load(file)
    except (json.JSONDecodeError, OSError):
        raw_sources = []

    normalized_sources = []
    seen_urls = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            continue

        normalized_source = normalize_source_config(raw_source)
        if not normalized_source["name"] or not normalized_source["url"]:
            continue
        if normalized_source["url"] in seen_urls:
            continue

        seen_urls.add(normalized_source["url"])
        normalized_sources.append(normalized_source)

    return normalized_sources


def save_source_configs(sources):
    CONFIG_SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_SOURCES_FILE.open("w", encoding="utf-8") as file:
        json.dump(sources, file, ensure_ascii=False, indent=2)


def load_source_state():
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            raw_states = json.load(file)
    except (json.JSONDecodeError, OSError):
        raw_states = []

    state_by_url = {}
    for raw_state in raw_states:
        if not isinstance(raw_state, dict):
            continue

        normalized_state = normalize_source_state(raw_state)
        if normalized_state["url"]:
            state_by_url[normalized_state["url"]] = normalized_state

    return state_by_url


def save_source_state(sources):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state_payload = [
        {
            "url": source["url"],
            "last_checked": source.get("last_checked", ""),
            "last_hash": source.get("last_hash", ""),
            "last_diff": source.get("last_diff", []),
            "last_text": source.get("last_text", ""),
        }
        for source in sources
    ]
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(state_payload, file, ensure_ascii=False, indent=2)


def load_sources():
    source_configs = load_source_configs()
    source_state = load_source_state()
    merged_sources = []

    for source_config in source_configs:
        state = source_state.get(source_config["url"], {})
        merged_sources.append(
            {
                "name": source_config["name"],
                "url": source_config["url"],
                "last_checked": state.get("last_checked", ""),
                "last_hash": state.get("last_hash", ""),
                "last_diff": state.get("last_diff", []),
                "last_text": state.get("last_text", ""),
            }
        )

    return merged_sources


def load_settings():
    if not SETTINGS_FILE.exists():
        save_settings({"priority_keywords": DEFAULT_PRIORITY_KEYWORDS})
        return {"priority_keywords": DEFAULT_PRIORITY_KEYWORDS.copy()}

    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as file:
            settings = json.load(file)
    except (json.JSONDecodeError, OSError):
        settings = {}

    priority_keywords = settings.get("priority_keywords", DEFAULT_PRIORITY_KEYWORDS)
    return {"priority_keywords": clean_priority_keywords(priority_keywords)}


def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_FILE.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


def fetch_page_text(url):
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "event-checker-bot/1.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    lines = []
    for raw_line in soup.get_text(separator="\n").splitlines():
        line = " ".join(raw_line.split()).strip()
        if line:
            lines.append(line)

    return "\n".join(lines)


def calculate_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_diff(old_text, new_text, priority_keywords):
    added_lines = []
    seen = set()

    for line in ndiff(old_text.splitlines(), new_text.splitlines()):
        if not line.startswith("+ "):
            continue

        candidate = line[2:].strip()
        if len(candidate) <= 3:
            continue
        if any(keyword in candidate for keyword in EXCLUDE_KEYWORDS):
            continue
        if candidate in seen:
            continue

        seen.add(candidate)
        added_lines.append(candidate)

    priority_lines = [
        line for line in added_lines
        if any(keyword in line for keyword in priority_keywords)
    ]
    normal_lines = [
        line for line in added_lines
        if not any(keyword in line for keyword in priority_keywords)
    ]

    return priority_lines + normal_lines


def ensure_sources_file():
    if not CONFIG_SOURCES_FILE.exists():
        save_source_configs([])


def ensure_settings_file():
    if not SETTINGS_FILE.exists():
        save_settings({"priority_keywords": DEFAULT_PRIORITY_KEYWORDS})


def build_status_message(requested_status):
    status_map = {
        "checked": "差分チェックを実行しました。",
    }
    return status_map.get(requested_status, "")


@app.route("/", methods=["GET"])
def index():
    ensure_sources_file()
    ensure_settings_file()
    return render_template(
        "index.html",
        sources=load_sources(),
        priority_keywords=load_settings()["priority_keywords"],
        message=build_status_message(request.args.get("status", "")),
        config_sources_path=CONFIG_SOURCES_FILE.name,
        config_settings_path=SETTINGS_FILE.name,
    )


@app.route("/check", methods=["POST"])
def check_sources():
    ensure_sources_file()
    ensure_settings_file()
    sources = load_sources()
    settings = load_settings()

    for source in sources:
        checked_at = datetime.now(timezone.utc).isoformat()
        old_text = source.get("last_text", "")
        old_hash = source.get("last_hash", "")

        try:
            new_text = fetch_page_text(source["url"])
            new_hash = calculate_hash(new_text)

            if not old_text:
                source["last_diff"] = ["初回チェック完了"]
            elif old_hash != new_hash:
                diff_lines = extract_diff(old_text, new_text, settings["priority_keywords"])
                source["last_diff"] = diff_lines or ["変更はありましたが表示対象の差分はありません"]
            else:
                source["last_diff"] = []

            source["last_text"] = new_text
            source["last_hash"] = new_hash
            source["last_checked"] = checked_at
        except requests.RequestException as error:
            source["last_checked"] = checked_at
            source["last_diff"] = [f"取得エラー: {error}"]
        except Exception as error:  # noqa: BLE001
            source["last_checked"] = checked_at
            source["last_diff"] = [f"処理エラー: {error}"]

    save_source_state(sources)
    return redirect(url_for("index", status="checked"))


ensure_sources_file()
ensure_settings_file()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
