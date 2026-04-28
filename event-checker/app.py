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
EXPORT_FORMAT_VERSION = 1
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


class ImportValidationError(ValueError):
    pass


def normalize_source(source):
    return {
        "name": str(source.get("name", "")).strip(),
        "url": str(source.get("url", "")).strip(),
        "last_checked": str(source.get("last_checked", "")).strip(),
        "last_hash": str(source.get("last_hash", "")).strip(),
        "last_diff": [str(line).strip() for line in source.get("last_diff", []) if str(line).strip()],
        # Internal snapshot storage is required to calculate future diffs.
        "last_text": str(source.get("last_text", "")),
    }


def clean_priority_keywords(priority_keywords):
    cleaned_keywords = []
    for keyword in priority_keywords:
        normalized = str(keyword).strip()
        if normalized and normalized not in cleaned_keywords:
            cleaned_keywords.append(normalized)

    if not cleaned_keywords:
        cleaned_keywords = DEFAULT_PRIORITY_KEYWORDS.copy()

    return cleaned_keywords


def load_sources():
    if not SOURCES_FILE.exists():
        save_sources([])
        return []

    try:
        with SOURCES_FILE.open("r", encoding="utf-8") as file:
            sources = json.load(file)
    except (json.JSONDecodeError, OSError):
        sources = []

    return [normalize_source(source) for source in sources]


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
    if not SOURCES_FILE.exists():
        save_sources([])


def ensure_settings_file():
    if not SETTINGS_FILE.exists():
        save_settings({"priority_keywords": DEFAULT_PRIORITY_KEYWORDS})


def build_export_payload():
    return {
        "format": "event-checker-export",
        "version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "sources": load_sources(),
        "settings": load_settings(),
    }


def parse_import_payload(raw_payload):
    if isinstance(raw_payload, list):
        imported_sources = validate_sources(raw_payload)
        return imported_sources, load_settings()

    if not isinstance(raw_payload, dict):
        raise ImportValidationError("JSONの形式が不正です。")

    if "sources" in raw_payload:
        imported_sources = validate_sources(raw_payload.get("sources", []))
        imported_settings = validate_settings(raw_payload.get("settings", {}))
        return imported_sources, imported_settings

    if "priority_keywords" in raw_payload:
        return load_sources(), validate_settings(raw_payload)

    raise ImportValidationError("読み込めるJSON形式ではありません。")


def validate_sources(raw_sources):
    if not isinstance(raw_sources, list):
        raise ImportValidationError("sources は配列で指定してください。")

    normalized_sources = []
    seen_urls = set()

    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise ImportValidationError("sources の各要素はオブジェクトで指定してください。")

        normalized_source = normalize_source(raw_source)
        if not normalized_source["name"] or not normalized_source["url"]:
            raise ImportValidationError("各URLデータには name と url が必要です。")
        if normalized_source["url"] in seen_urls:
            raise ImportValidationError("同じURLが複数含まれています。")

        seen_urls.add(normalized_source["url"])
        normalized_sources.append(normalized_source)

    return normalized_sources


def validate_settings(raw_settings):
    if not isinstance(raw_settings, dict):
        raise ImportValidationError("settings はオブジェクトで指定してください。")

    return {
        "priority_keywords": clean_priority_keywords(
            raw_settings.get("priority_keywords", DEFAULT_PRIORITY_KEYWORDS)
        )
    }


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
        "imported": "JSONをインポートしました。",
        "import_empty": "インポートするJSONファイルを選択してください。",
        "import_invalid": "JSONの読み込みに失敗しました。形式を確認してください。",
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


@app.route("/export", methods=["GET"])
def export_data():
    ensure_sources_file()
    ensure_settings_file()

    payload = build_export_payload()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    response_body = json.dumps(payload, ensure_ascii=False, indent=2)

    return app.response_class(
        response=response_body,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="event-checker-export-{timestamp}.json"'
        },
    )


@app.route("/import", methods=["POST"])
def import_data():
    ensure_sources_file()
    ensure_settings_file()

    uploaded_file = request.files.get("import_file")
    if uploaded_file is None or not uploaded_file.filename:
        return redirect(url_for("index", status="import_empty"))

    try:
        raw_text = uploaded_file.read().decode("utf-8")
        raw_payload = json.loads(raw_text)
        imported_sources, imported_settings = parse_import_payload(raw_payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ImportValidationError):
        return redirect(url_for("index", status="import_invalid"))

    save_sources(imported_sources)
    save_settings(imported_settings)
    return redirect(url_for("index", status="imported"))


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
