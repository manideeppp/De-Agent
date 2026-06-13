import os
import ssl
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (one level up from backend/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from twilio.twiml.voice_response import VoiceResponse

app = Flask(__name__)
CORS(app)

PORTAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")

DB_PATH = "reminders.db"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            message TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            timezone TEXT DEFAULT 'UTC',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def row_to_dict(row):
    return dict(row) if row else None

# ---------------------------------------------------------------------------
# Twilio helper
# ---------------------------------------------------------------------------

def make_call(reminder_id, phone, message, name):
    """Place an outbound Twilio call for the given reminder using inline TwiML."""
    if not phone.startswith("+"):
        phone = "+" + phone

    # Use custom HTTP client to bypass Zscaler SSL interception on corporate network
    http_client = TwilioHttpClient()
    http_client.session.verify = False
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, http_client=http_client)

    # Build TwiML inline so no public callback URL is needed
    twiml_response = VoiceResponse()
    text = (
        f"Hello {name}, this is your reminder. "
        f"{message}. "
        f"This was an automated reminder call. Goodbye!"
    )
    twiml_response.say(text, voice="Polly.Joanna", language="en-US")
    twiml_response.hangup()

    call = client.calls.create(
        to=phone,
        from_=TWILIO_PHONE_NUMBER,
        twiml=str(twiml_response)
    )
    return call.sid

# ---------------------------------------------------------------------------
# Scheduler job
# ---------------------------------------------------------------------------

def check_due_reminders():
    """Run every 60s — find pending reminders that are due and call them."""
    # Use IST (UTC+5:30)
    from datetime import timezone, timedelta as td
    ist = timezone(td(hours=5, minutes=30))
    now = datetime.now(ist).strftime("%Y-%m-%dT%H:%M")
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE status = 'pending' AND scheduled_at <= ?",
        (now,)
    ).fetchall()

    for row in rows:
        reminder = row_to_dict(row)
        try:
            make_call(reminder["id"], reminder["phone"], reminder["message"], reminder["name"])
            conn.execute("UPDATE reminders SET status = 'called' WHERE id = ?", (reminder["id"],))
            conn.commit()
            print(f"[Scheduler] Called reminder #{reminder['id']} for {reminder['name']}")
        except Exception as e:
            conn.execute("UPDATE reminders SET status = 'failed' WHERE id = ?", (reminder["id"],))
            conn.commit()
            print(f"[Scheduler] Failed reminder #{reminder['id']}: {e}")

    conn.close()

# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(PORTAL_DIR, "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/reminders", methods=["GET"])
def list_reminders():
    conn = get_db()
    rows = conn.execute("SELECT * FROM reminders ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/reminders", methods=["POST"])
def create_reminder():
    data = request.get_json(force=True)

    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    message = data.get("message", "").strip()
    scheduled_at = data.get("scheduled_at", "").strip()
    timezone = data.get("timezone", "UTC").strip()

    if not all([name, phone, message, scheduled_at]):
        return jsonify({"error": "name, phone, message, and scheduled_at are required"}), 400

    # Ensure phone has +
    if not phone.startswith("+"):
        phone = "+" + phone

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO reminders (name, phone, message, scheduled_at, timezone) VALUES (?, ?, ?, ?, ?)",
        (name, phone, message, scheduled_at, timezone)
    )
    conn.commit()
    reminder_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    conn.close()

    return jsonify(row_to_dict(row)), 201


@app.route("/api/reminders/<int:reminder_id>", methods=["DELETE"])
def delete_reminder(reminder_id):
    conn = get_db()
    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/reminders/<int:reminder_id>/call-now", methods=["POST"])
def call_now(reminder_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "Reminder not found"}), 404

    reminder = row_to_dict(row)

    try:
        call_sid = make_call(reminder["id"], reminder["phone"], reminder["message"], reminder["name"])
        conn.execute("UPDATE reminders SET status = 'called' WHERE id = ?", (reminder_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "call_sid": call_sid})
    except Exception as e:
        conn.execute("UPDATE reminders SET status = 'failed' WHERE id = ?", (reminder_id,))
        conn.commit()
        conn.close()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# TwiML endpoint (Twilio fetches this during the call)
# ---------------------------------------------------------------------------

@app.route("/twiml/<int:reminder_id>", methods=["GET", "POST"])
def twiml(reminder_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    conn.close()

    response = VoiceResponse()

    if row:
        reminder = row_to_dict(row)
        text = (
            f"Hello {reminder['name']}, this is your reminder. "
            f"{reminder['message']}. "
            f"This was an automated reminder call. Goodbye!"
        )
        response.say(text, voice="Polly.Joanna", language="en-US")
    else:
        response.say("Sorry, this reminder was not found. Goodbye!", voice="Polly.Joanna", language="en-US")

    response.hangup()
    return Response(str(response), mimetype="application/xml")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()

    # Start the background scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_due_reminders, "interval", seconds=60)
    scheduler.start()
    print("[Scheduler] Started — checking for due reminders every 60 seconds")

    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
