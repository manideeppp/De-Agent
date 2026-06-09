# 📞 Voice Call Reminder Agent

A full-stack web app that calls people on their phone at a scheduled time and reads them a reminder message. Deployable on **Vercel** (serverless).

---

## How It Works

```
┌──────────────┐        ┌──────────────────┐        ┌──────────┐        ┌───────────┐
│   Browser    │  HTTP   │  Vercel Serverless│  API    │  Twilio  │  Call   │   Phone   │
│  (Frontend)  │───────▶│  (Python Flask)   │───────▶│  Cloud   │───────▶│  (User)   │
│              │◀───────│                  │◀───────│          │        │           │
└──────────────┘        └──────────────────┘        └──────────┘        └───────────┘
                                │                        │
                                │   GET /api/twiml?id=X  │
                                │◀───────────────────────│
                                │   (TwiML response)     │
```

**Flow:**
1. User creates a reminder via the portal (name, phone, message, time)
2. API stores it in Postgres with status "pending"
3. Vercel Cron hits `/api/cron` every minute to check for due reminders
4. When a reminder is due, it calls Twilio's API to initiate an outbound call
5. Twilio calls the user's phone and fetches `/api/twiml?id=X` from your server
6. Your server returns TwiML XML telling Twilio what to say
7. Twilio reads the message aloud to the person, then hangs up

---

## Project Structure (Vercel-ready)

```
voice-reminder/
├── api/
│   └── index.py            ← Serverless Flask function (all API routes)
├── public/
│   └── index.html          ← Frontend (auto-served by Vercel)
├── backend/
│   ├── app.py              ← Local dev server (SQLite, APScheduler)
│   └── requirements.txt    ← Local dev dependencies
├── requirements.txt        ← Vercel dependencies (Postgres)
├── vercel.json             ← Vercel routing & cron config
└── README.md
```

---

## Deploy to Vercel

### 1. Create a Postgres database

Use one of these (all have free tiers):
- [Neon](https://neon.tech) (recommended — generous free tier)
- [Vercel Postgres](https://vercel.com/storage/postgres)
- [Supabase](https://supabase.com)

Copy the connection string (looks like `postgresql://user:pass@host/dbname`).

### 2. Sign up for Twilio

- Go to [twilio.com](https://www.twilio.com/try-twilio)
- Free trial = 75 minutes of calls, no credit card needed
- Get your **Account SID**, **Auth Token**, and a **phone number**

### 3. Push to GitHub

```bash
cd voice-reminder
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/voice-reminder.git
git push -u origin main
```

### 4. Import to Vercel

1. Go to [vercel.com/new](https://vercel.com/new)
2. Import your GitHub repo
3. Set **Root Directory** to `voice-reminder` (if it's nested) or leave as default
4. Add **Environment Variables**:

| Variable | Value |
|----------|-------|
| `TWILIO_ACCOUNT_SID` | `ACxxxxxxxxxxxxx` |
| `TWILIO_AUTH_TOKEN` | `your_auth_token` |
| `TWILIO_PHONE_NUMBER` | `+15551234567` |
| `DATABASE_URL` | `postgresql://user:pass@host/db` |
| `BASE_URL` | `https://your-app.vercel.app` |
| `CRON_SECRET` | `any-random-string` (optional) |

5. Click **Deploy**!

### 5. After deploying

- Update `BASE_URL` env var to match your actual Vercel URL (e.g., `https://voice-reminder-abc.vercel.app`)
- Redeploy for the change to take effect
- The Vercel Cron (defined in `vercel.json`) will automatically call `/api/cron` every minute

---

## Local Development

For local dev, use the original `backend/app.py` which uses SQLite + APScheduler (no Postgres needed):

```bash
cd voice-reminder/backend
pip install -r requirements.txt

export TWILIO_ACCOUNT_SID=ACxxx
export TWILIO_AUTH_TOKEN=xxx
export TWILIO_PHONE_NUMBER=+15551234567
export BASE_URL=https://your-ngrok-url.ngrok-free.app

python app.py
```

Then open http://localhost:5000 in your browser.

For Twilio to reach your local server, use ngrok:
```bash
ngrok http 5000
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/reminders` | List all reminders |
| POST | `/api/reminders` | Create reminder |
| DELETE | `/api/reminders/<id>` | Delete reminder |
| POST | `/api/reminders/<id>/call-now` | Trigger immediate call |
| GET/POST | `/api/twiml?id=<id>` | TwiML for Twilio |
| GET/POST | `/api/cron` | Process due reminders (called by Vercel Cron) |

### Create Reminder (example)
```json
POST /api/reminders
{
  "name": "John Doe",
  "phone": "+919515320303",
  "message": "Your dentist appointment is in 30 minutes",
  "scheduled_at": "2025-01-15T10:30"
}
```

---

## Cost

| Service | Cost |
|---------|------|
| Vercel (Hobby) | Free |
| Neon Postgres | Free tier (0.5 GB) |
| Twilio Free Trial | 75 minutes of calls |
| Twilio after trial | ~$0.014/min (US), ~₹1-2/min (India) |

---

## Notes

- On Twilio free trial, you can only call **verified numbers** (add them in Twilio console)
- `BASE_URL` must be your public Vercel URL so Twilio can reach `/api/twiml`
- Vercel Cron runs every minute (Pro plan) or every 1 hour minimum (Hobby plan — upgrade schedule in vercel.json if needed)
- For Hobby plan, you can use an external cron service like [cron-job.org](https://cron-job.org) to hit `/api/cron` every minute
