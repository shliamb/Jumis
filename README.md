**English** · [Русский](README.ru.md)

# Jumis

A personal AI secretary that lives in your Telegram. A userbot (Telethon) reads your private
chats and stores them in your own PostgreSQL, and an aiogram bot lets you ask questions,
search the history, manage memory and send messages on your behalf.

## What it does

- **Owns your inbox.** Every private message you receive (and send) is saved to your database and
  vectorised. Unanswered conversations are collected in one widget; it clears itself when you reply.
- **Answers as you.** It drafts replies in your tone, using your saved style and facts. Messages are
  sent only when you ask — there is no autopilot.
- **Remembers.** Three layers of memory: always-visible rules, response styles and a personal
  passport; an archive of facts with semantic search; nightly cleanup that merges duplicates
  instead of deleting them.
- **Understands voice.** Voice messages are transcribed locally (no cloud), and you can talk to the
  bot instead of typing.
- **Keeps time.** Reminders, alarms and cron tasks run through the scheduler.
- **Reads the news.** A daily digest from RSS feeds, written by the model with source links.
- **Maintains itself.** Junk cleanup, old conversations compressed into digests, nightly database
  backups, automatic schema updates on start.

## How it works

Two Telegram interfaces in one process:

- **owner bot** (aiogram) — the chat where you talk to Jumis: questions, commands, admin panel;
- **userbot** (Telethon) — your own account, used to read and send private messages.

The agent works through a set of narrow tools (search, facts, send message, tasks, news, backups)
instead of one giant prompt, so a request costs little. The model is called through LiteLLM, so
providers and models can be swapped in config. Everything is stored locally in PostgreSQL with
pgvector; only the LLM request leaves the machine.

## Stack

| Layer | What |
| --- | --- |
| Language | Python 3.12 |
| Telegram | `aiogram` 3.31 (bot), `telethon` 1.44 (userbot) |
| Models | `litellm` 1.93 — DeepSeek by default, others configurable |
| Database | PostgreSQL 17 + `pgvector` (Docker image `pgvector/pgvector:pg17`), `asyncpg`, `psycopg2` |
| Embeddings | `sentence-transformers`, `multilingual-e5-base` (768 dims, local) |
| Speech | `kairos-asr` (local, GPU) |
| Runtime | `docker compose` for the database, `uv`/`pip` for Python packages |

## Requirements

- Linux, Docker and Docker Compose
- Python 3.12
- `ffmpeg`, `libsndfile1`
- An NVIDIA GPU for local speech recognition (`kairos-asr`); text features work without it
- Telegram `api_id` / `api_hash` from [my.telegram.org](https://my.telegram.org) and a bot token
  from [@BotFather](https://t.me/BotFather)
- An LLM API key (DeepSeek by default)

## Installation

```bash
git clone https://github.com/shliamb/Jumis.git
cd Jumis

cp .env.example .env          # fill in the values, see Configuration
cp .env.llm.example .env.llm  # LLM provider keys

docker compose up -d postgres # PostgreSQL 17 + pgvector

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install "kairos-asr[gpu]" sentence-transformers

.venv/bin/python jumis/run_bot.py
```

First start:

1. Send `/start` to your bot — it registers you as the owner.
2. Send `/login` and confirm the QR link in Telegram (or enter the code). This creates
   `session_jumis.session` in the project root — the userbot's authorisation.
3. Tables are created automatically at start. `/crTabDb` creates them manually if needed.

> A full image, `install.sh` and a compose file that runs the bot as a container are the next step.
> When that lands, the session file must be mounted from the host (`SESSION_PATH`), so a login
> survives container rebuilds.

## Configuration

`.env`:

| Variable | Meaning |
| --- | --- |
| `telegram_bot_token` | bot token from @BotFather |
| `admin_id` | your Telegram user id — the only account with access |
| `api_id`, `api_hash` | Telegram API credentials for the userbot |
| `USER_DB`, `PASSWORD_DB`, `POSTGRES_DB` | PostgreSQL credentials (match `docker-compose.yml`) |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SESSION_PATH` | optional, path to the Telethon session file |

`.env.llm`: provider keys — `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`ANTHROPIC_API_KEY`, `XAI_API_KEY`. Adding a key makes those models available in `/getMod`.

Both files are git-ignored. The database stores your message history, so keep it private.

## Usage

Talk to the bot like a person: text or voice. It answers with streaming and can offer buttons with
options when a question has clear choices.

Commands:

| Command | Purpose |
| --- | --- |
| `/admin` | menu, userbot status, all admin commands |
| `/login` | connect or re-connect the userbot (QR or code) |
| `/getMod`, `/setMod` | list models / choose the default one |
| `/cleanup`, `/digests` | history cleanup and compressed digests |
| `/backup`, `/backups` | database backup copies |
| `/rss` | news sources and digest |
| `/logs` | download logs |
| `/reset` | restart the bot |

## Project layout

```
jumis/
  run_bot.py        entry point: everything in one asyncio process
  config.py         settings, paths, session location
  handlers/         aiogram routers (admin panel, login, options, rss, …)
  jumis_agent/      the agent: streaming, tool loop, inbox widget
  llm/              LiteLLM layer, system prompt, tool definitions
  memory/           three-layer memory and nightly dedupe
  telethoner/       userbot session, login machine, alerts
  ingest/           incoming messages → database → vectorisation
  database/         schema, repositories
  cleaner/          junk cleanup and history digests
  pgbackup/         nightly pg_dump
  rss/              news feeds and digests
  scheduler/        reminders, alarms, cron
```

Every module has a `SPEC.md` describing its inputs, outputs and logic; `MEMORY.md` is the map of
the whole project.

## Status

Personal project, single owner, private chats only. It sends messages only on explicit request and
never replies to anyone on its own. Work in progress — container packaging and a one-command
installer are next.
