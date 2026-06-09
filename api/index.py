import os
import requests as http_requests
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
BASE_URL = os.environ.get("BASE_URL", "https://deeagentt.vercel.app")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", os.environ.get("NEXT_PUBLIC_SUPABASE_URL", ""))
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", ""))

# Supabase REST API base
SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def supabase_get(table, params=None):
    """GET from Supabase REST API."""
    url = f"{SUPABASE_REST}/{table}"
    headers = {k: v for k, v in SUPABASE_HEADERS.items() if k != "Prefer"}
    res = http_requests.get(url, headers=headers, params=params or {})
    res.raise_for_status()
    return res.json()


def supabase_post(table, data):
    """POST (insert) to Supabase REST API."""
    url = f"{SUPABASE_REST}/{table}"
    res = http_requests.post(url, headers=SUPABASE_HEADERS, json=data)
    res.raise_for_status()
    return res.json()


def supabase_patch(table, match_params, data):
    """PATCH (update) rows matching params."""
    url = f"{SUPABASE_REST}/{table}"
    res = http_requests.patch(url, headers=SUPABASE_HEADERS, params=match_params, json=data)
    res.raise_for_status()
    return res.json()


def supabase_delete(table, match_params):
    """DELETE rows matching params."""
    url = f"{SUPABASE_REST}/{table}"
    headers = {k: v for k, v in SUPABASE_HEADERS.items() if k != "Prefer"}
    res = http_requests.delete(url, headers=headers, params=match_params)
    res.raise_for_status()
    return True

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
# API Routes
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/reminders", methods=["GET"])
def list_reminders():
    rows = supabase_get("reminders", {"order": "id.desc"})
    return jsonify(rows)


@app.route("/api/reminders", methods=["POST"])
def create_reminder():
    data = request.get_json(force=True)

    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    message = data.get("message", "").strip()
    scheduled_at = data.get("scheduled_at", "").strip()
    timezone_str = data.get("timezone", "IST").strip()

    if not all([name, phone, message, scheduled_at]):
        return jsonify({"error": "name, phone, message, and scheduled_at are required"}), 400

    # Ensure phone has +
    if not phone.startswith("+"):
        phone = "+" + phone

    row_data = {
        "name": name,
        "phone": phone,
        "message": message,
        "scheduled_at": scheduled_at,
        "timezone": timezone_str,
        "status": "pending"
    }

    result = supabase_post("reminders", row_data)
    return jsonify(result[0] if result else row_data), 201


@app.route("/api/reminders/<int:reminder_id>", methods=["DELETE"])
def delete_reminder(reminder_id):
    supabase_delete("reminders", {"id": f"eq.{reminder_id}"})
    return jsonify({"success": True})


@app.route("/api/reminders/<int:reminder_id>/call-now", methods=["POST"])
def call_now(reminder_id):
    rows = supabase_get("reminders", {"id": f"eq.{reminder_id}"})

    if not rows:
        return jsonify({"error": "Reminder not found"}), 404

    reminder = rows[0]

    try:
        call_sid = make_call(reminder["id"], reminder["phone"], reminder["name"])
        supabase_patch("reminders", {"id": f"eq.{reminder_id}"}, {"status": "called"})
        return jsonify({"success": True, "call_sid": call_sid})
    except Exception as e:
        supabase_patch("reminders", {"id": f"eq.{reminder_id}"}, {"status": "failed"})
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# TwiML endpoint (Twilio fetches this during the call)
# ---------------------------------------------------------------------------

@app.route("/api/twiml", methods=["GET", "POST"])
def twiml():
    reminder_id = request.args.get("id")
    response = VoiceResponse()

    if reminder_id:
        rows = supabase_get("reminders", {"id": f"eq.{reminder_id}"})

        if rows:
            reminder = rows[0]
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

    now = datetime.now(IST).strftime("%Y-%m-%dT%H:%M")

    # Get pending reminders where scheduled_at <= now
    rows = supabase_get("reminders", {
        "status": "eq.pending",
        "scheduled_at": f"lte.{now}",
        "order": "id.asc"
    })

    results = []
    for reminder in rows:
        try:
            make_call(reminder["id"], reminder["phone"], reminder["name"])
            supabase_patch("reminders", {"id": f"eq.{reminder['id']}"}, {"status": "called"})
            results.append({"id": reminder["id"], "status": "called"})
        except Exception as e:
            supabase_patch("reminders", {"id": f"eq.{reminder['id']}"}, {"status": "failed"})
            results.append({"id": reminder["id"], "status": "failed", "error": str(e)})

    return jsonify({"processed": len(results), "results": results, "checked_at": now})


# ---------------------------------------------------------------------------
# For local development
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
