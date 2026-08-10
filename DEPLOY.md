# SendSMS - Nigerian SMS Outreach CRM and Automation Platform

A production-ready, self-hosted SMS outreach platform built for Nigerian businesses. Uses [SMS-Gate.app](https://sms-gate.app) as the primary SMS gateway through an Android device.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.x, Celery |
| Frontend | React 18, TypeScript, Tailwind CSS, Recharts |
| Database | PostgreSQL 16 |
| Queue/Broker | Redis 7 |
| SMS Gateway | [SMS-Gate.app](https://sms-gate.app) (Android) |
| Auth | Argon2id, JWT, HTTP-only cookies, CSRF |
| Deployment | Render / Docker |

## Features

- **Dashboard** — Real-time metrics (contacts, messages, deliveries, replies), charts
- **Contacts** — CRUD, search, filter, bulk actions, CSV import/export
- **Lists** — Group contacts, manage memberships
- **Campaigns** — Create, validate, start, pause, resume, stop, duplicate
- **Sequence Builder** — Visual automation (Send SMS → Wait → Condition → Stop)
- **Follow-ups** — Due today, overdue, upcoming, completed views
- **Inbox** — Full SMS conversation threads with reply, stop/resume sequence
- **Templates** — SMS templates with variables (`{{first_name}}`, `{{business_name}}`, etc.)
- **Analytics** — Delivery rate, reply rate, opt-outs, interested leads
- **Settings** — SMS Gateway, push notifications, compliance, sending rules
- **Dark mode** — System-aware with manual toggle
- **Mobile responsive** — Works on phones, tablets, and desktops

## Table of Contents

1. [Local Development](#local-development)
2. [Docker Deployment](#docker-deployment)
3. [Render Deployment](#render-deployment)
4. [SMS-Gate.app Setup](#sms-gateapp-setup)
5. [Production Checklist](#production-checklist)
6. [Troubleshooting](#troubleshooting)

---

## 1. Local Development

### Prerequisites

| Tool | Minimum Version | How to Check |
|------|----------------|--------------|
| Python | 3.12+ | `python3 --version` |
| Node.js | 22+ | `node --version` |
| PostgreSQL | 16+ | `psql --version` |
| Redis | 7+ | `redis-cli --version` |
| Git | Any | `git --version` |

### Step-by-Step

#### 1. Clone the Repository

```bash
git clone <your-repo-url> sendsms
cd sendsms
```

#### 2. Create Environment File

```bash
cp .env.example .env
```

Edit `.env` with your local values:

```ini
# REQUIRED
DATABASE_URL=postgresql+asyncpg://sendsms:sendsms@localhost:5432/sendsms
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=generate-a-random-string-at-least-32-characters-long
CREDENTIAL_ENCRYPTION_KEY=another-random-string-at-least-32-characters

# Bootstrap admin (only used on first run to create the admin account)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-strong-password-here

# Optional
APP_ENV=development
LOG_LEVEL=DEBUG
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
DEFAULT_TIMEZONE=Africa/Lagos
```

**Generate secure keys:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Use the output for SECRET_KEY

python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Use the output for CREDENTIAL_ENCRYPTION_KEY
```

#### 3. Create the Database

```bash
# If PostgreSQL is running locally:
sudo -u postgres psql -c "CREATE USER sendsms WITH PASSWORD 'sendsms';"
sudo -u postgres psql -c "CREATE DATABASE sendsms OWNER sendsms;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE sendsms TO sendsms;"
```

#### 4. Set Up Python Backend

```bash
# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate       # Linux/macOS
# OR
.venv\Scripts\activate           # Windows PowerShell

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

#### 5. Install Frontend Dependencies

```bash
npm install
```

#### 6. Start Everything (4 Terminals)

**Terminal 1 — Redis** (if not already running):

```bash
redis-server
```

**Terminal 2 — Backend API:**

```bash
source .venv/bin/activate
cd backend
PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

You should see:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Started reloader process
```

Verify: open http://localhost:8000/api/v1/health — you should see `{"status":"ok",...}`

**Terminal 3 — Celery Worker:**

```bash
source .venv/bin/activate
cd backend
PYTHONPATH=. celery -A app.tasks.celery_app worker --loglevel=INFO
```

You should see the Celery banner and `celery@hostname ready`.

**Terminal 4 — Frontend Dev Server:**

```bash
npm run dev
```

You should see:

```
  VITE v6.x  ready in xxx ms
  ➜  Local:   http://localhost:5173/
```

#### 7. Open the Application

Visit **http://localhost:5173** and log in with the credentials you set in `.env`.

The first login triggers automatic creation of the admin account from `ADMIN_USERNAME`/`ADMIN_PASSWORD`. After that, the password is hashed with Argon2id, and the env vars are no longer used for authentication.

---

## 2. Docker Deployment

### Prerequisites

- Docker Engine 24+
- Docker Compose v2+

### Step-by-Step

#### 1. Clone and Configure

```bash
git clone <your-repo-url> sendsms
cd sendsms

# Generate secrets
echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env.docker
echo "CREDENTIAL_ENCRYPTION_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env.docker
```

Edit `docker-compose.yml` and replace the `SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD` values with production-safe secrets.

> **⚠️ Never commit `docker-compose.yml` with real secrets to Git.** Use environment variable files or Docker secrets in production.

#### 2. Build and Start

```bash
docker compose build
docker compose up -d
```

This starts 4 containers:

| Container | Port | Description |
|-----------|------|-------------|
| `postgres` | 5432 | PostgreSQL database |
| `redis` | 6379 | Redis for Celery queue |
| `backend` | 8000 | FastAPI REST API |
| `worker` | — | Celery background worker |
| `frontend` | 3000 | Nginx serving React app |

#### 3. Verify

```bash
# Check container status
docker compose ps

# Check backend health
curl http://localhost:8000/api/v1/health

# Check frontend
curl -I http://localhost:3000
```

#### 4. View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f worker
```

#### 5. Stop

```bash
docker compose down
```

To also delete all data (volumes):

```bash
docker compose down -v
```

---

## 3. Render Deployment (Free Tier — No Credit Card)

There are three paths, pick the one that works for you:

| Path | Effort | Needs Card? | What You Get |
|------|--------|-------------|--------------|
| **A: Blueprint** | 10 min | No | 2 Render services + Render PostgreSQL + Render Redis |
| **B: Manual** | 15 min | No | Same as A, step-by-step in the UI |
| **C: Zero Card** | 20 min | No | 2 Render services + Neon (free DB) + Upstash (free Redis) |

---

### Path A: Blueprint (Easiest — No Card)

The `render.yaml` only creates compute services (web + worker). You create the database and Redis manually first, then the Blueprint wires them together.

#### Step A1: Create PostgreSQL Manually

1. Go to **[dashboard.render.com](https://dashboard.render.com)** → sign up with GitHub
2. Click **New +** → **PostgreSQL**
3. Fill in:

   | Field | Value |
   |-------|-------|
   | Name | `sendsms-db` |
   | Database | `sendsms` |
   | User | `sendsms` |
   | Region | Frankfurt |
   | Plan | **Free** |

4. Click **Create Database**
5. **Copy the "Internal Database URL"** — save it somewhere (looks like `postgresql://sendsms:xxxx@sendsms-db.internal:5432/sendsms`)

#### Step A2: Create Redis Manually

1. Click **New +** → **Redis**
2. Fill in:

   | Field | Value |
   |-------|-------|
   | Name | `sendsms-redis` |
   | Region | Frankfurt |
   | Plan | **Free** |

3. Click **Create Redis**
4. **Copy the "Internal Redis URL"** — save it (looks like `redis://sendsms-redis.internal:6379`)

#### Step A3: Apply the Blueprint

1. Click **New +** → **Blueprint**
2. Select `ghostauto01-boop/SENDERSMS`, branch `arena/019fe940-sendersms`
3. Render shows 2 services: `sendsms-api` and `sendsms-worker` (no databases — those are already created)
4. Click **Apply**

#### Step A4: Fill in Environment Variables

After the Blueprint creates the services, go to **sendsms-api → Environment** and set:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | *(paste the Internal Database URL from A1)* |
| `REDIS_URL` | *(paste the Internal Redis URL from A2)* |
| `ADMIN_USERNAME` | `admin` |
| `ADMIN_PASSWORD` | *(a strong password you choose)* |
| `CORS_ORIGINS` | *(leave empty or use `*` for testing)* |

Do the same for **sendsms-worker**. The deploy will start automatically when you save the env vars.

#### Step A5: Verify

```bash
curl https://sendsms-api.onrender.com/api/v1/health
```

Open `https://sendsms-api.onrender.com` and log in.

---

### Path B: Manual (No Blueprint — No Card)

Do everything through the Render UI, one service at a time.

#### Step B1: Create PostgreSQL

Same as Path A Step 1 above. Save the **Internal Database URL**.

#### Step B2: Create Redis

Same as Path A Step 2 above. Save the **Internal Redis URL**.

#### Step B3: Create Web Service

1. Click **New +** → **Web Service** → Connect `ghostauto01-boop/SENDERSMS`
2. Fill in:

   | Field | Value |
   |-------|-------|
   | Name | `sendsms-api` |
   | Region | Frankfurt |
   | Branch | `arena/019fe940-sendersms` |
   | Runtime | Python 3 |
   | Build Command | `pip install --upgrade pip && pip install -r requirements.txt` |
   | Start Command | `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | Plan | **Free** |
   | Health Check Path | `/api/v1/health` |

3. Add Environment Variables (same list as Path A Step 4)
4. Click **Create Web Service**

#### Step B4: Create Background Worker

1. Click **New +** → **Background Worker** → Select `ghostauto01-boop/SENDERSMS`
2. Fill in:

   | Field | Value |
   |-------|-------|
   | Name | `sendsms-worker` |
   | Region | Frankfurt |
   | Branch | `arena/019fe940-sendersms` |
   | Build Command | `pip install --upgrade pip && pip install -r requirements.txt` |
   | Start Command | `cd backend && celery -A app.tasks.celery_app worker --loglevel=INFO -B` |
   | Plan | **Free** |

3. Add the same Environment Variables as Step B3
4. Click **Create Background Worker**

---

### Path C: Zero Card (External Free Databases)

If Render still asks for a card even for free PostgreSQL/Redis, use external providers that have genuinely free tiers with no card:

#### Step C1: Free PostgreSQL — [Neon](https://neon.tech)

1. Go to **[neon.tech](https://neon.tech)** → Sign up with GitHub (no card)
2. Click **Create Project** → name it `sendsms`
3. Copy the connection string from the dashboard
4. It looks like: `postgresql://sendsms:password@ep-xxx.us-east-2.aws.neon.tech/sendsms?sslmode=require`

> **Important:** The URL from Neon uses `postgresql://`. Our app auto-converts it to `postgresql+asyncpg://` at startup, so just paste it as-is.

#### Step C2: Free Redis — [Upstash](https://upstash.com)

1. Go to **[console.upstash.com](https://console.upstash.com)** → Sign up with GitHub (no card)
2. Click **Create Redis Database** → name it `sendsms-redis`
3. Choose region close to Frankfurt
4. Copy the **REST URL** from the dashboard (the `rediss://` URL)

#### Step C3: Deploy to Render

Now apply the Blueprint. The `render.yaml` creates **2 free web services** — the worker is bundled as a web service because Render's free tier doesn't support the "worker" type.

| Service | Runs | URL |
|---------|------|-----|
| `sendsms-api` | FastAPI (uvicorn) | `https://sendsms-api.onrender.com` |
| `sendsms-worker` | Celery worker + mini health server | internal only |

**Applying the Blueprint:**

1. Go to **[dashboard.render.com](https://dashboard.render.com)**
2. Click **New +** → **Blueprint**
3. Select `ghostauto01-boop/SENDERSMS`, branch `arena/019fe940-sendersms`
4. Render shows 2 services — click **Apply**

**Setting environment variables (do this for BOTH services):**

Go to each service → **Environment** and set:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Paste the Neon URL from Step C1 |
| `REDIS_URL` | Paste the Upstash URL from Step C2 |
| `SECRET_KEY` | Paste key #1 from Step 3 |
| `CREDENTIAL_ENCRYPTION_KEY` | Paste key #2 from Step 3 |
| `ADMIN_USERNAME` | `admin` |
| `ADMIN_PASSWORD` | *(pick a strong password)* |

Click **Save Changes** on each — Render redeploys automatically.

**Verify:**

```bash
curl https://sendsms-api.onrender.com/api/v1/health
# → {"status":"ok","app":"SendSMS",...}
```

Then open `https://sendsms-api.onrender.com` and log in.

---

### Render Environment Variables Reference

After deployment, here's what you need to set:

| Variable | Required | Source |
|----------|----------|--------|
| `DATABASE_URL` | **Yes** | Paste from PostgreSQL provider |
| `REDIS_URL` | **Yes** | Paste from Redis provider |
| `SECRET_KEY` | **Yes** | Auto-generated by Blueprint, or generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CREDENTIAL_ENCRYPTION_KEY` | **Yes** | Same as above — generate a second one |
| `ADMIN_USERNAME` | **Yes** | Your admin username (e.g. `admin`) |
| `ADMIN_PASSWORD` | **Yes** | Your strong admin password |
| `APP_ENV` | Yes | `production` |
| `LOG_LEVEL` | No | `INFO` (default) |
| `CORS_ORIGINS` | No | Frontend URL(s), comma-separated |
| `DEFAULT_TIMEZONE` | No | `Africa/Lagos` (default) |

> **SMS Gateway and push notifications** can be configured from within the app (Settings page) after deployment — no env vars needed.

### Free Tier Notes

- Render web service spins down after 15 min idle (30-60s cold start on wake)
- Render free PostgreSQL expires after 90 days
- Neon free tier: 0.5 GB storage, 100 compute hours/month
- Upstash free tier: 256 MB, 10,000 commands/day
- Total cost: **$0.00**



---

## 4. SMS-Gate.app Setup

### Overview

[SMS-Gate.app](https://sms-gate.app) is an Android app that turns your phone into an SMS gateway. The app exposes a REST API on your local network.

### Step-by-Step

#### 1. Install SMS-Gate.app

1. Download from [Google Play Store](https://play.google.com/store/apps/details?id=network.smsgate.app) or SMS-Gate.app website
2. Install on the Android phone that will send/receive SMS
3. Open the app and go through initial setup

#### 2. Configure SMS-Gate.app

1. Note the **Server URL** shown in the app (e.g., `http://192.168.1.100:8080`)
2. Set a **Username** and **Password** for API access
3. (Optional) Set an **API Key** for additional security
4. Note your **Device ID** if you have multiple devices

#### 3. Expose SMS-Gate.app to the Internet

Since your Render-deployed backend needs to reach the Android device:

**Option A — ngrok (free, easiest for testing):**

```bash
# Install ngrok
# Then tunnel the SMS-Gate.app port:
ngrok http 8080
# Note the public URL (e.g., https://abc123.ngrok.io)
```

**Option B — Cloudflare Tunnel (more stable):**

```bash
# Install cloudflared
cloudflared tunnel create sendsms-gateway
cloudflared tunnel route dns sendsms-gateway sms.yourdomain.com
cloudflared tunnel run --url http://localhost:8080 sendsms-gateway
```

**Option C — Static IP / DDNS (for production):**

If your internet has a static IP, set up port forwarding on your router and use a DDNS service.

#### 4. Configure in SendSMS

Open your SendSMS dashboard, go to **Settings → SMS Gateway**, and enter:

| Field | Value |
|-------|-------|
| Base URL | Your public/exposed URL (e.g., `https://abc123.ngrok.io`) |
| Username | SMS-Gate.app username |
| Password | SMS-Gate.app password |
| Device ID | (Optional) Device ID from app |
| Sender ID | (Optional) Your sender name/number |

Click **Test Connection**. If it says "Connection successful!", enable the gateway.

#### 5. Webhook Setup (Inbound SMS)

For real-time incoming SMS, SMS-Gate.app can send webhooks to your Render URL:

1. In Settings → SMS Gateway, note the **Webhook URL** shown
2. In SMS-Gate.app, configure the webhook to point to:
   ```
   https://sendsms-api.onrender.com/api/v1/webhooks/smsgate/inbound
   ```
3. Set a **Webhook Secret** in both SMS-Gate.app and SendSMS settings (for signature validation)

If webhooks aren't available, the system falls back to polling automatically.

---

## 5. Production Checklist

Before going live, verify each item:

### Security

- [ ] `SECRET_KEY` is a random 32+ character string
- [ ] `CREDENTIAL_ENCRYPTION_KEY` is a random 32+ character string
- [ ] `ADMIN_PASSWORD` is strong (12+ chars, mixed case, numbers, symbols)
- [ ] `APP_ENV` is set to `production`
- [ ] CORS origins are locked to your actual frontend domain (not `*`)
- [ ] `.env` is in `.gitignore` and not committed
- [ ] No secrets in `docker-compose.yml` (use env files or secrets manager)
- [ ] Render env vars with `sync: false` are set manually
- [ ] Change admin password after first login

### Database

- [ ] PostgreSQL has daily backups configured (Render does this automatically on paid plans)
- [ ] Database connection uses SSL (Render provisioned DBs have this by default)

### SMS Gateway

- [ ] SMS-Gate.app connection tested and healthy
- [ ] Test SMS sent successfully
- [ ] Inbound webhook tested (send an SMS to the device and check Inbox)
- [ ] Webhook secret configured for signature validation
- [ ] Sending hours configured appropriately (default: 08:00–20:00 Africa/Lagos)
- [ ] Daily/hourly/minute limits reviewed

### Compliance

- [ ] Consent requirement enabled (Settings → Compliance)
- [ ] Auto opt-out detection enabled
- [ ] Global suppression list enabled
- [ ] Opt-out keywords tested (send STOP to trigger)

### Monitoring

- [ ] Health endpoint responding: `GET /api/v1/health`
- [ ] Celery worker is running and processing tasks
- [ ] Render logs are monitored for errors
- [ ] Dashboard shows correct gateway status

### Frontend (if deployed separately)

- [ ] API URL points to your Render backend
- [ ] CORS origins include your frontend domain
- [ ] HTTPS is enforced
- [ ] Test login flow
- [ ] Test all main pages load without errors

---

## 6. Troubleshooting

### Backend won't start

**Symptom:** `ModuleNotFoundError: No module named 'app'`

**Fix:** Make sure you're running from the `backend/` directory with `PYTHONPATH=.`:

```bash
cd backend && PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Database connection refused

**Symptom:** `could not connect to server: Connection refused`

**Fix:**
1. Verify PostgreSQL is running: `pg_isready`
2. Check your `DATABASE_URL` host/port
3. If using Docker, make sure the postgres container is healthy: `docker compose ps`

### Celery worker stuck or not processing

**Symptom:** Messages stay in "queued" status

**Fix:**
1. Check Redis is running: `redis-cli ping` (should return `PONG`)
2. Check worker logs: `docker compose logs worker`
3. Restart the worker: `docker compose restart worker`

### SMS not sending

**Symptom:** Messages marked as "failed"

**Fix:**
1. Verify SMS Gateway is enabled in Settings → SMS Gateway
2. Test the connection there
3. Check the gateway device has:
   - Active SIM card with credit
   - Mobile data or WiFi
   - SMS-Gate.app running in foreground
4. Check the ngrok/tunnel URL is still active
5. Check worker logs for specific error messages

### Inbound SMS not appearing

**Symptom:** Replies don't show in Inbox

**Fix:**
1. Verify webhook URL is configured in SMS-Gate.app
2. Check `/api/v1/webhooks/logs` endpoint for received events
3. Check if the phone number is being normalized correctly
4. Check worker logs for `process_inbound_sms` task execution

### Frontend shows blank page

**Symptom:** White screen after login

**Fix:**
1. Check browser console for errors (F12 → Console)
2. Verify API URL in the frontend build matches your backend URL
3. Clear browser cache and cookies
4. Check CORS settings include your frontend origin

### Render deploy fails

**Symptom:** Build fails on Render

**Fix:**
1. Check build logs in Render dashboard
2. Common issues:
   - Missing `requirements.txt` → Make sure it's in the repo root
   - Python version mismatch → Set `PYTHON_VERSION=3.12.0` env var
   - Build command failing → Check `render.yaml` `buildCommand`
3. Push a new commit to trigger a re-deploy

---

## Project Structure

```
SENDERSMS/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry point
│   │   ├── config.py            # Pydantic settings
│   │   ├── database.py          # SQLAlchemy async engine
│   │   ├── models/              # 22 database tables
│   │   ├── schemas/             # Pydantic request/response models
│   │   ├── api/v1/              # 12 route modules
│   │   ├── services/            # Business logic layer
│   │   ├── providers/           # SMS + Notification interfaces
│   │   ├── tasks/               # Celery background jobs
│   │   ├── security/            # Auth, encryption, CSRF
│   │   └── utils/               # Phone numbers, helpers
│   └── tests/                   # pytest test suite (58 tests)
├── frontend/
│   ├── src/
│   │   ├── pages/               # 10 page components
│   │   ├── layouts/             # MainLayout with sidebar
│   │   ├── api/                 # Axios client
│   │   ├── hooks/               # Auth context
│   │   └── types/               # TypeScript interfaces
│   └── index.html
├── Dockerfile                   # Multi-stage (backend, worker, frontend)
├── docker-compose.yml           # Local dev stack
├── render.yaml                  # Render blueprint
├── requirements.txt             # Python dependencies
├── package.json                 # Node dependencies
├── nginx.conf                   # Frontend nginx config
├── .env.example                 # Environment template
└── README.md                    # This file
```
