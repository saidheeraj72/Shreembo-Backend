# Enterprise Intelligence Hub — Backend

FastAPI backend for the Enterprise Intelligence Hub. Provides RAG-powered chat, document management, meeting notes, and organisation management via a WebSocket + REST API.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [1. Install Python 3.13](#1-install-python-313)
- [2. Install System Dependencies](#2-install-system-dependencies)
- [3. Install Redis](#3-install-redis)
- [4. Clone & Set Up the Project](#4-clone--set-up-the-project)
- [5. Configure Environment Variables](#5-configure-environment-variables)
- [6. Run Database Migrations](#6-run-database-migrations)
- [7. Run the Server](#7-run-the-server)
- [8. Running with Docker](#8-running-with-docker)
- [Project Structure](#project-structure)
- [API Overview](#api-overview)

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.13+ |
| Redis | 7+ |
| Supabase project | (free tier works) |
| OpenAI API key | GPT-5 / embeddings |

---

## 1. Install Python 3.13

### macOS

```bash
# Using Homebrew (recommended)
brew install python@3.13

# Verify
python3.13 --version
```

### Ubuntu / Debian

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.13 python3.13-venv python3.13-dev

# Verify
python3.13 --version
```

### Windows

Download the installer from [python.org/downloads](https://www.python.org/downloads/) and run it.
Check **"Add Python to PATH"** during installation.

```powershell
# Verify in a new terminal
python --version
```

---

## 2. Install System Dependencies

`python-magic` (file-type detection) requires the native `libmagic` library.

### macOS

```bash
brew install libmagic
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install libmagic1 libmagic-dev
```

### Windows

```powershell
pip install python-magic-bin
```

> `python-magic-bin` bundles the DLL on Windows — no extra install needed.

---

## 3. Install Redis

Redis is used for caching and rate limiting.

### macOS

```bash
brew install redis
brew services start redis   # start and auto-restart on login
```

### Ubuntu / Debian

```bash
sudo apt install redis-server
sudo systemctl enable --now redis-server
```

### Windows

Use [Memurai](https://www.memurai.com/) (Redis-compatible) or run Redis via WSL2.

Verify Redis is running:

```bash
redis-cli ping   # should respond: PONG
```

---

## 4. Clone & Set Up the Project

### Option A — Using `uv` (recommended, faster)

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# or: pip install uv

# Clone the repo
git clone <your-repo-url>
cd backend

# Create virtual environment and install dependencies
uv venv --python 3.13
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

uv sync
```

### Option B — Using `pip`

```bash
git clone <your-repo-url>
cd backend

python3.13 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. Configure Environment Variables

Create a `.env` file in the project root (copy the block below and fill in your values):

```env
# ── Application ───────────────────────────────────────────────────────────────
PROJECT_NAME="Enterprise Intelligence Hub"
ENVIRONMENT=development
DEBUG=false

# ── CORS ──────────────────────────────────────────────────────────────────────
BACKEND_CORS_ORIGINS=["http://localhost:5173","http://localhost:8080"]

# ── Supabase ──────────────────────────────────────────────────────────────────
# Dashboard → Project Settings → API
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<your-anon-key>
SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>
SUPABASE_JWT_SECRET=<your-jwt-secret>

# ── Supabase Storage (S3-compatible) ──────────────────────────────────────────
# Dashboard → Project Settings → Storage → S3 Access Keys → Create new
AWS_ACCESS_KEY_ID=<supabase-s3-access-key-id>
AWS_SECRET_ACCESS_KEY=<supabase-s3-secret-access-key>
AWS_S3_BUCKET=<your-bucket-name>
AWS_S3_REGION=ap-south-1
AWS_S3_ENDPOINT_URL=https://<project-ref>.storage.supabase.co/storage/v1/s3

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
# REDIS_PASSWORD=your-redis-password   # uncomment if Redis has auth

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...

# ── Web Search — optional ─────────────────────────────────────────────────────
# Free key at https://serper.dev
SERPER_API_KEY=<your-serper-key>

# ── Email — Resend, optional ──────────────────────────────────────────────────
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=noreply@yourdomain.com
EMAIL_ENABLED=true

# ── Frontend URL ──────────────────────────────────────────────────────────────
FRONTEND_URL=http://localhost:5173
```

### Where to find each Supabase value

| Variable | Location in Supabase Dashboard |
|---|---|
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_ANON_KEY` | Project Settings → API → `anon public` |
| `SUPABASE_SERVICE_ROLE_KEY` | Project Settings → API → `service_role secret` |
| `SUPABASE_JWT_SECRET` | Project Settings → API → JWT Secret |
| S3 access keys | Project Settings → Storage → S3 Access Keys → **Create new** |
| `AWS_S3_ENDPOINT_URL` | `https://<project-ref>.storage.supabase.co/storage/v1/s3` |

> **Supabase Storage bucket**: go to **Storage → New bucket**, create a private bucket, and set its name as `AWS_S3_BUCKET`.

---

## 6. Run Database Migrations

All migrations are plain SQL files in `migrations/`. Run them in the order listed below.

### Using the Supabase SQL Editor (simplest)

1. Open your project at [supabase.com](https://supabase.com)
2. Navigate to **SQL Editor**
3. Open and run each file in this order:

```
migrations/document_repository_schema.sql    ← run first
migrations/enterprise_schema.sql
migrations/chat_schema.sql
migrations/add_message_attachments.sql
migrations/add_usage_limits.sql
migrations/add_user_access_controls.sql
migrations/add_meetings_tables.sql
migrations/fix_node_permissions_constraint.sql
migrations/update_session_documents_schema.sql
migrations/add_thinking_to_messages.sql
migrations/add_chat_context_nodes.sql
migrations/add_email_accounts.sql
migrations/add_email_issue_scans.sql          ← run last
```

### Using the Supabase CLI

```bash
# Install CLI
brew install supabase/tap/supabase   # macOS
# or: npm install -g supabase

# Link to your project
supabase link --project-ref <your-project-ref>

# Run files one by one
supabase db execute --file migrations/document_repository_schema.sql
# repeat for each file in the order above
```

---

## 7. Run the Server

Make sure your virtual environment is activated and `.env` exists in the project root.

### Development (hot reload)

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### Production

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
```

The server will be available at:

| URL | Description |
|---|---|
| `http://localhost:8000/health` | Health check |
| `http://localhost:8000/docs` | Swagger UI — interactive API docs |
| `http://localhost:8000/redoc` | ReDoc documentation |
| `ws://localhost:8000/api/v1/chat/ws` | Chat WebSocket |

---

## 8. Running with Docker

A `Dockerfile` is included for containerised deployments.

### Build and run

```bash
docker build -t eih-backend .

docker run -d \
  --name eih-backend \
  -p 8000:8000 \
  --env-file .env \
  eih-backend
```

### With Docker Compose (backend + Redis together)

Create `docker-compose.yml` in the project root:

```yaml
services:
  backend:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - REDIS_HOST=redis
    depends_on:
      - redis
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis_data:/data

volumes:
  redis_data:
```

```bash
docker compose up -d
docker compose logs -f backend
```

---

## Project Structure

```
backend/
├── src/
│   ├── main.py                 # FastAPI app, middleware, lifespan
│   ├── config.py               # All settings via pydantic-settings
│   ├── api/v1/                 # Route handlers
│   │   ├── chat_routes/        # Sessions, WebSocket, messages
│   │   ├── document_routes/    # Upload, list, search, folders
│   │   └── ...
│   ├── core/
│   │   ├── database.py         # Supabase client
│   │   ├── openai_client.py    # OpenAI embeddings
│   │   ├── qdrant_client.py    # Local vector DB (file-based)
│   │   ├── s3.py               # Supabase Storage (S3-compatible)
│   │   └── chat_websocket.py   # WebSocket connection manager
│   ├── llm/
│   │   ├── rag.py              # RAG service composition
│   │   ├── rag_generation.py   # Agentic: tool-calling → rerank → stream
│   │   ├── rag_retrieval.py    # Qdrant vector search + permissions
│   │   ├── reranker.py         # BM25+RRF / FlashRank reranker
│   │   ├── embedding.py        # Document embedding pipeline
│   │   └── web_search.py       # Serper web search
│   ├── documents/              # Document CRUD + S3 upload
│   ├── chat/                   # Chat sessions + history
│   ├── meeting/                # Meeting transcription + AI notes
│   └── auth/                   # JWT + Supabase auth
├── migrations/                 # SQL files — run against Supabase
├── requirements.txt
├── pyproject.toml              # uv / PEP 517 config
├── Dockerfile
└── .env                        # ← create this (never commit)
```

---

## API Overview

| Group | Base path | Notes |
|---|---|---|
| Auth | `/api/v1/auth` | Login, register, refresh |
| Chat | `/api/v1/chat` | Sessions, messages, WebSocket stream |
| Documents | `/api/v1/documents` | Upload, search, folders |
| Meetings | `/api/v1/meetings` | Transcription, AI-generated notes |
| Usage | `/api/v1/usage` | Token usage stats |
| Admin | `/api/v1/admin` | Organisation management |
| Health | `/health` | Liveness probe |

Full interactive docs are at **`/docs`** when the server is running.

---

## Optional: Neural Reranker

For higher RAG accuracy install FlashRank (adds a neural cross-encoder on top of the default BM25+RRF reranker):

```bash
pip install flashrank
# or: uv add flashrank
```

It activates automatically on the next server start. No code changes required.

---

## Troubleshooting

**`libmagic` not found on startup**
→ macOS: `brew install libmagic` | Ubuntu: `sudo apt install libmagic1`

**Redis connection refused**
→ macOS: `brew services start redis` | Linux: `sudo systemctl start redis`

**Supabase JWT errors / 401 on every request**
→ `SUPABASE_JWT_SECRET` must exactly match the value in Supabase Dashboard → Project Settings → API → JWT Secret

**S3 upload / presigned URL errors**
→ Confirm the bucket exists in Supabase Storage and `AWS_S3_BUCKET` in `.env` matches the exact bucket name. The bucket must be **private**.

**`No module named 'magic'` on Windows**
→ Run `pip install python-magic-bin` instead of `python-magic`
