import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
BASE_URL = os.environ.get("BASE_URL", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            message TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            timezone TEXT DEFAULT 'UTC',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def row_to_dict(row, columns):
    return dict(zip(columns, row)) if row else None


def rows_to_list(rows, columns):
    return [dict(zip(columns, row)) for row in rows]


REMINDER_COLUMNS = ["id", "name", "phone", "message", "scheduled_at", "timezone", "status", "created_at"]

# ---------------------------------------------------------------------------
# Twilio helper
# ---------------------------------------------------------------------------

def make_call(reminder_id, phone, name):
    """Place an outbound Twilio call for the given reminder."""
    if not phone.startswith("+"):
        phone = "+" + phone

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    twiml_url = f"{BASE_URL}/api/twiml?id={reminder_id}"

    call = client.calls.create(
        to=phone,
        from_=TWILIO_PHONE_NUMBER,
        url=twiml_url
    )
    return call.sid

# ---------------------------------------------------------------------------
# Initialize DB on cold start
# ---------------------------------------------------------------------------
if DATABASE_URL:
    try:
        init_db()
    except Exception as e:
        print(f"[DB Init] Warning: {e}")

# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/reminders", methods=["GET"])
def list_reminders():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, phone, message, scheduled_at, timezone, status, created_at FROM reminders ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = rows_to_list(rows, REMINDER_COLUMNS)
    # Convert datetime objects to strings
    for r in result:
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
    return jsonify(result)


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
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reminders (name, phone, message, scheduled_at, timezone) VALUES (%s, %s, %s, %s, %s) RETURNING id, name, phone, message, scheduled_at, timezone, status, created_at",
        (name, phone, message, scheduled_at, timezone)
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    result = row_to_dict(row, REMINDER_COLUMNS)
    if result.get("created_at") and hasattr(result["created_at"], "isoformat"):
        result["created_at"] = result["created_at"].isoformat()
    return jsonify(result), 201


@app.route("/api/reminders/<int:reminder_id>", methods=["DELETE"])
def delete_reminder(reminder_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/reminders/<int:reminder_id>/call-now", methods=["POST"])
def call_now(reminder_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, phone, message, scheduled_at, timezone, status, created_at FROM reminders WHERE id = %s", (reminder_id,))
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return jsonify({"error": "Reminder not found"}), 404

    reminder = row_to_dict(row, REMINDER_COLUMNS)

    try:
        call_sid = make_call(reminder["id"], reminder["phone"], reminder["name"])
        cur.execute("UPDATE reminders SET status = 'called' WHERE id = %s", (reminder_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "call_sid": call_sid})
    except Exception as e:
        cur.execute("UPDATE reminders SET status = 'failed' WHERE id = %s", (reminder_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# TwiML endpoint (Twilio fetches this during the call)
# ---------------------------------------------------------------------------

@app.route("/api/twiml", methods=["GET", "POST"])
def twiml():
    reminder_id = request.args.get("id")
    response = VoiceResponse()

    if reminder_id:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name, phone, message, scheduled_at, timezone, status, created_at FROM reminders WHERE id = %s", (int(reminder_id),))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            reminder = row_to_dict(row, REMINDER_COLUMNS)
            text = (
                f"Hello {reminder['name']}, this is your reminder. "
                f"{reminder['message']}. "
                f"This was an automated reminder call. Goodbye!"
            )
            response.say(text, voice="Polly.Joanna", language="en-US")
        else:
            response.say("Sorry, this reminder was not found. Goodbye!", voice="Polly.Joanna", language="en-US")
    else:
        response.say("Sorry, no reminder specified. Goodbye!", voice="Polly.Joanna", language="en-US")

    response.hangup()
    return Response(str(response), mimetype="application/xml")


# ---------------------------------------------------------------------------
# Cron endpoint — processes due reminders
# ---------------------------------------------------------------------------

@app.route("/api/cron", methods=["GET", "POST"])
def cron():
    # Optional: verify cron secret for security
    auth = request.headers.get("Authorization", "")
    if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
        return jsonify({"error": "Unauthorized"}), 401

    # Use IST (UTC+5:30)
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist).strftime("%Y-%m-%dT%H:%M")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, phone, message, scheduled_at, timezone, status, created_at FROM reminders WHERE status = 'pending' AND scheduled_at <= %s",
        (now,)
    )
    rows = cur.fetchall()
    results = []

    for row in rows:
        reminder = row_to_dict(row, REMINDER_COLUMNS)
        try:
            make_call(reminder["id"], reminder["phone"], reminder["name"])
            cur.execute("UPDATE reminders SET status = 'called' WHERE id = %s", (reminder["id"],))
            conn.commit()
            results.append({"id": reminder["id"], "status": "called"})
        except Exception as e:
            cur.execute("UPDATE reminders SET status = 'failed' WHERE id = %s", (reminder["id"],))
            conn.commit()
            results.append({"id": reminder["id"], "status": "failed", "error": str(e)})

    cur.close()
    conn.close()
    return jsonify({"processed": len(results), "results": results})


# ---------------------------------------------------------------------------
# For local development
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
