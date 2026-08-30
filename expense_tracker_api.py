#!/usr/bin/env python3
"""Expense tracker and budget manager REST API (Flask + SQLite)."""

import argparse
import random
import sqlite3
from datetime import date, datetime, timedelta

from flask import Flask, g, jsonify, request

DB_PATH = "expense_tracker_api.db"
DATE_FORMAT = "%Y-%m-%d"

app = Flask(__name__)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_date TEXT NOT NULL,
            expense_item TEXT NOT NULL,
            expense_category TEXT NOT NULL,
            expense_amount REAL NOT NULL,
            UNIQUE(expense_item, expense_date)
        )
        """
    )
    conn.commit()
    conn.close()


def parse_date(value: str) -> date:
    return datetime.strptime(value, DATE_FORMAT).date()


@app.errorhandler(404)
def not_found(_err):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(_err):
    return jsonify({"error": "Internal server error"}), 500


@app.post("/track_expense")
def track_expense():
    data = request.get_json(silent=True) or request.form or request.args

    required = ["expense_date", "expense_item", "expense_category", "expense_amount"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

    expense_date = data["expense_date"]
    expense_item = str(data["expense_item"]).strip()
    expense_category = str(data["expense_category"]).strip().lower()

    try:
        parse_date(expense_date)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid expense_date, expected format YYYY-MM-DD"}), 400

    try:
        expense_amount = float(data["expense_amount"])
    except (TypeError, ValueError):
        return jsonify({"error": "expense_amount must be a number"}), 400
    if expense_amount <= 0:
        return jsonify({"error": "expense_amount must be greater than 0"}), 400

    if not expense_item or not expense_category:
        return jsonify({"error": "expense_item and expense_category must not be empty"}), 400

    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO expenses (expense_date, expense_item, expense_category, expense_amount)
               VALUES (?, ?, ?, ?)""",
            (expense_date, expense_item, expense_category, expense_amount),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return (
            jsonify(
                {
                    "error": "Duplicate entry: an expense with this expense_item and "
                    "expense_date already exists. Not saved."
                }
            ),
            409,
        )

    return (
        jsonify(
            {
                "id": cur.lastrowid,
                "expense_date": expense_date,
                "expense_item": expense_item,
                "expense_category": expense_category,
                "expense_amount": expense_amount,
            }
        ),
        201,
    )


@app.get("/view_expense_data")
def view_expense_data():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    category = request.args.get("category")

    if not start_date or not end_date:
        return jsonify({"error": "start_date and end_date are required (format YYYY-MM-DD)"}), 400

    try:
        start = parse_date(start_date)
        end = parse_date(end_date)
    except ValueError:
        return jsonify({"error": "Invalid date format, expected YYYY-MM-DD"}), 400

    if start > end:
        return jsonify({"error": "start_date must not be after end_date"}), 400

    query = "SELECT * FROM expenses WHERE expense_date BETWEEN ? AND ?"
    params = [start_date, end_date]
    if category:
        query += " AND expense_category = ?"
        params.append(category.strip().lower())
    query += " ORDER BY expense_date"

    db = get_db()
    rows = db.execute(query, params).fetchall()
    expenses = [dict(row) for row in rows]
    total = sum(r["expense_amount"] for r in expenses)

    return jsonify({"count": len(expenses), "total_amount": round(total, 2), "expenses": expenses})


def seed_expenses() -> None:
    """Create 20 expense entries between 2026-01-01 and 2026-05-31 via /track_expense."""
    init_db()
    items_by_category = {
        "food": ["Groceries", "Restaurant", "Takeout", "Coffee"],
        "transport": ["Uber", "Fuel", "Bus fare", "Parking"],
        "rent": ["Monthly rent", "Utilities"],
    }
    start = date(2026, 1, 1)
    end = date(2026, 5, 31)
    span_days = (end - start).days

    client = app.test_client()
    created = 0
    attempts = 0
    while created < 20 and attempts < 200:
        attempts += 1
        category = random.choice(list(items_by_category.keys()))
        item = random.choice(items_by_category[category])
        expense_date = start + timedelta(days=random.randint(0, span_days))
        amount = round(random.uniform(20, 1500) if category == "rent" else random.uniform(10, 500), 2)

        resp = client.post(
            "/track_expense",
            json={
                "expense_date": expense_date.strftime(DATE_FORMAT),
                "expense_item": item,
                "expense_category": category,
                "expense_amount": amount,
            },
        )
        if resp.status_code == 201:
            created += 1
            print(f"Created: {expense_date} {category:<10} {item:<15} ${amount}")

    print(f"\nSeeded {created} expense entries into {DB_PATH}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expense tracker and budget manager API.")
    parser.add_argument(
        "command",
        nargs="?",
        default="runserver",
        choices=["runserver", "seed"],
        help="'runserver' starts the Flask API (default), 'seed' creates 20 sample entries.",
    )
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    init_db()
    if args.command == "seed":
        seed_expenses()
    else:
        app.run(debug=True, port=args.port)
