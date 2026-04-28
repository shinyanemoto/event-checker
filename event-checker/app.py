import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask, redirect, render_template, request, url_for
from difflib import ndiff


BASE_DIR = Path(__file__).resolve().parent
SOURCES_FILE = Path(os.environ.get("SOURCES_PATH", BASE_DIR / "sources.json"))
SETTINGS_FILE = Path(os.environ.get("SETTINGS_PATH", BASE_DIR / "settings.json"))
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


def load_sources():
    if not SOURCES_FILE.exists():
        save_sources([])
        return []

    try:
        with SOURCES_FILE.open("r", encoding="utf-8") as file:
            sources = json.load(file)
    except (json.JSONDecodeError, OSError):
        sources = []

    normalized_sources = []
    for source in sources:
        normalized_sources.append(
            {
                "name": source.get("name", "").strip(),
                "url": source.get("url", "").strip(),
                "last_checked": source.get("last_checked", ""),
                "last_hash": source.get("last_hash", ""),
                "last_diff": source.get("last_diff", []),
                # Internal snapshot storage is required to calculate future diffs.
                "last_text": source.get("last_text", ""),
            }
        )

    return normalized_sources


def save_sources(sources):
    SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SOURCES_FILE.open("w", encoding="utf-8") as file:
        json.dump(sources, file, ensure_ascii=False, indent=2)


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
    cleaned_keywords = []
    for keyword in priority_keywords:
        normalized = str(keyword).strip()
        if normalized and normalized not in cleaned_keywords:
            cleaned_keywords.append(normalized)

    if not cleaned_keywords:
        cleaned_keywords = DEFAULT_PRIORITY_KEYWORDS.copy()

    return {"priority_keywords": cleaned_keywords}


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
    if not SOURCES_FILE.exists():
        save_sources([])


def ensure_settings_file():
    if not SETTINGS_FILE.exists():
        save_settings({"priority_keywords": DEFAULT_PRIORITY_KEYWORDS})


def build_status_message(requested_status):
    status_map = {
        "added": "URLを登録しました。",
        "deleted": "URLを削除しました。",
        "checked": "差分チェックを実行しました。",
        "empty": "名前とURLを入力してください。",
        "duplicate": "同じURLはすでに登録されています。",
        "keyword_added": "重要キーワードを追加しました。",
        "keyword_empty": "重要キーワードを入力してください。",
        "keyword_duplicate": "同じ重要キーワードはすでに登録されています。",
    }
    return status_map.get(requested_status, "")


@app.route("/", methods=["GET"])
def index():
    ensure_sources_file()
    ensure_settings_file()
    sources = load_sources()
    settings = load_settings()
    message = build_status_message(request.args.get("status", ""))
    return render_template(
        "index.html",
        sources=sources,
        priority_keywords=settings["priority_keywords"],
        message=message,
    )


@app.route("/add", methods=["POST"])
def add_source():
    ensure_sources_file()
    sources = load_sources()

    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()

    if not name or not url:
        return redirect(url_for("index", status="empty"))

    if any(source["url"] == url for source in sources):
        return redirect(url_for("index", status="duplicate"))

    sources.append(
        {
            "name": name,
            "url": url,
            "last_checked": "",
            "last_hash": "",
            "last_diff": [],
            "last_text": "",
        }
    )
    save_sources(sources)

    return redirect(url_for("index", status="added"))


@app.route("/delete", methods=["POST"])
def delete_source():
    ensure_sources_file()
    sources = load_sources()

    url = request.form.get("url", "").strip()
    updated_sources = [source for source in sources if source["url"] != url]

    if len(updated_sources) != len(sources):
        save_sources(updated_sources)
        return redirect(url_for("index", status="deleted"))

    return redirect(url_for("index"))


@app.route("/keywords/add", methods=["POST"])
def add_keyword():
    ensure_settings_file()
    settings = load_settings()

    keyword = request.form.get("keyword", "").strip()
    if not keyword:
        return redirect(url_for("index", status="keyword_empty"))

    if keyword in settings["priority_keywords"]:
        return redirect(url_for("index", status="keyword_duplicate"))

    settings["priority_keywords"].append(keyword)
    save_settings(settings)
    return redirect(url_for("index", status="keyword_added"))


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

    save_sources(sources)
    return redirect(url_for("index", status="checked"))


ensure_sources_file()
ensure_settings_file()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
