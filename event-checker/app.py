import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "db.sqlite3"))

app = Flask(__name__)


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        url = request.form.get("url", "").strip()

        if name and url:
            created_at = datetime.now(timezone.utc).isoformat()
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO sources (name, url, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (name, url, created_at),
                )
                connection.commit()

        return redirect(url_for("index"))

    with get_connection() as connection:
        sources = connection.execute(
            """
            SELECT id, name, url, created_at
            FROM sources
            ORDER BY id DESC
            """
        ).fetchall()

    return render_template("index.html", sources=sources)


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
