"""
Cloud Payment System - Simple Base Version
------------------------------------------
A minimal REST API for accounts, deposits, withdrawals, transfers
and transaction history. Uses Flask + SQLite.

Run:
    pip install flask
    python app.py
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Flask, g, jsonify, request

DB_PATH = "payments.db"
app = Flask(__name__)


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id         TEXT PRIMARY KEY,
            owner      TEXT NOT NULL,
            balance    TEXT NOT NULL DEFAULT '0.00',
            currency   TEXT NOT NULL DEFAULT 'USD',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id           TEXT PRIMARY KEY,
            type         TEXT NOT NULL,          -- deposit | withdrawal | transfer
            from_account TEXT,
            to_account   TEXT,
            amount       TEXT NOT NULL,
            status       TEXT NOT NULL,          -- completed | failed
            created_at   TEXT NOT NULL
        );
        """
    )
    db.commit()
    db.close()


# ----------------------------------------------------------------- helpers
def now():
    return datetime.now(timezone.utc).isoformat()


def parse_amount(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def get_account(account_id):
    return get_db().execute(
        "SELECT * FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()


def record(tx_type, from_acc, to_acc, amount, status="completed"):
    tx_id = str(uuid.uuid4())
    get_db().execute(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?)",
        (tx_id, tx_type, from_acc, to_acc, str(amount), status, now()),
    )
    return tx_id


def error(message, code=400):
    return jsonify({"error": message}), code


# ------------------------------------------------------------------ routes
@app.post("/accounts")
def create_account():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    if not owner:
        return error("'owner' is required")
    account_id = str(uuid.uuid4())
    currency = (data.get("currency") or "USD").upper()
    db = get_db()
    db.execute(
        "INSERT INTO accounts VALUES (?,?,?,?,?)",
        (account_id, owner, "0.00", currency, now()),
    )
    db.commit()
    return jsonify({"id": account_id, "owner": owner,
                    "balance": "0.00", "currency": currency}), 201


@app.get("/accounts/<account_id>")
def read_account(account_id):
    acc = get_account(account_id)
    if not acc:
        return error("Account not found", 404)
    return jsonify(dict(acc))


@app.post("/accounts/<account_id>/deposit")
def deposit(account_id):
    amount = parse_amount((request.get_json(silent=True) or {}).get("amount"))
    if amount is None:
        return error("'amount' must be a positive number")
    acc = get_account(account_id)
    if not acc:
        return error("Account not found", 404)
    new_balance = Decimal(acc["balance"]) + amount
    db = get_db()
    db.execute("UPDATE accounts SET balance = ? WHERE id = ?",
               (str(new_balance), account_id))
    tx_id = record("deposit", None, account_id, amount)
    db.commit()
    return jsonify({"transaction_id": tx_id, "balance": str(new_balance)})


@app.post("/accounts/<account_id>/withdraw")
def withdraw(account_id):
    amount = parse_amount((request.get_json(silent=True) or {}).get("amount"))
    if amount is None:
        return error("'amount' must be a positive number")
    acc = get_account(account_id)
    if not acc:
        return error("Account not found", 404)
    balance = Decimal(acc["balance"])
    db = get_db()
    if balance < amount:
        record("withdrawal", account_id, None, amount, "failed")
        db.commit()
        return error("Insufficient funds", 402)
    new_balance = balance - amount
    db.execute("UPDATE accounts SET balance = ? WHERE id = ?",
               (str(new_balance), account_id))
    tx_id = record("withdrawal", account_id, None, amount)
    db.commit()
    return jsonify({"transaction_id": tx_id, "balance": str(new_balance)})


@app.post("/transfer")
def transfer():
    data = request.get_json(silent=True) or {}
    src_id, dst_id = data.get("from_account"), data.get("to_account")
    amount = parse_amount(data.get("amount"))
    if not src_id or not dst_id or src_id == dst_id:
        return error("Provide two different 'from_account' and 'to_account'")
    if amount is None:
        return error("'amount' must be a positive number")
    src, dst = get_account(src_id), get_account(dst_id)
    if not src or not dst:
        return error("Account not found", 404)
    if src["currency"] != dst["currency"]:
        return error("Currency mismatch")
    src_balance = Decimal(src["balance"])
    db = get_db()
    if src_balance < amount:
        record("transfer", src_id, dst_id, amount, "failed")
        db.commit()
        return error("Insufficient funds", 402)
    db.execute("UPDATE accounts SET balance = ? WHERE id = ?",
               (str(src_balance - amount), src_id))
    db.execute("UPDATE accounts SET balance = ? WHERE id = ?",
               (str(Decimal(dst["balance"]) + amount), dst_id))
    tx_id = record("transfer", src_id, dst_id, amount)
    db.commit()
    return jsonify({"transaction_id": tx_id, "status": "completed"})


@app.get("/accounts/<account_id>/transactions")
def history(account_id):
    if not get_account(account_id):
        return error("Account not found", 404)
    rows = get_db().execute(
        """SELECT * FROM transactions
           WHERE from_account = ? OR to_account = ?
           ORDER BY created_at DESC""",
        (account_id, account_id),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
