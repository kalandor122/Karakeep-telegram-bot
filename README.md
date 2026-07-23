# 🔖 Karakeep Telegram Bot

A Telegram bot that acts as a **middleman for [Karakeep](https://karakeep.app/)** (self-hosted bookmark manager). Send links, text, or images via Telegram — the bot forwards them to your Karakeep instance. **If Karakeep is offline, bookmarks are queued locally and retried automatically when it comes back online.**

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-ready-brightgreen)](https://docker.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## ✨ Features

- 📎 **Links, text & images** — send any type via Telegram
- 🔒 **Chat ID allowlist** — restrict to specific users (optional)
- 🔌 **Offline queue** — if Karakeep is unreachable, bookmarks go into a local SQLite queue
- ♻ **Auto-retry** — queued bookmarks are retried every 60 seconds until Karakeep comes back
- 🐳 **Docker** — one command deployment
- 🤖 **Simple commands** — `/start`, `/status`, `/flush`, `/setkey`

## 🚀 Quick Start

### 1. Create a Telegram bot

Talk to [@BotFather](https://t.me/BotFather) on Telegram:
```
/newbot
→ pick a name and username
→ copy the token
```

### 2. Get your Chat ID

Message [@userinfobot](https://t.me/userinfobot) on Telegram → it replies with your ID.

### 3. Get a Karakeep API key

In your Karakeep web UI: **Settings → API Keys** → generate a new key.

### 4. Deploy with Docker

```bash
git clone https://github.com/kalandor122/karakeep-telegram-bot.git
cd karakeep-telegram-bot
cp .env.example .env
# Edit .env with your bot token, chat ID, and Karakeep URL
docker compose up -d
```

## ⚙️ Configuration

Edit the `.env` file:

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `KARAKEEP_URL` | ✅ | Your Karakeep instance URL (e.g. `https://hoarder.magor-lab.hu`) |
| `KARAKEEP_TOKEN` | ❌ | Karakeep API key (can also be set via `/setkey` in chat) |
| `ALLOWED_USER_IDS` | ❌ | Comma-separated Telegram user IDs to restrict access |
| `RETRY_INTERVAL` | ❌ | Seconds between queue retry attempts (default: 60) |
| `LOG_LEVEL` | ❌ | Logging level (default: INFO) |

> 💡 You can set the API key at runtime too: just send `/setkey <key>` to the bot.

## 🤝 How it works

```
Telegram → [Bot] → Karakeep API
                   │
                   ├── ✅ Online → bookmark created!
                   └── ❌ Offline → queued in SQLite → retry every 60s
```

## 📖 Commands

| Command | Description |
|---------|-------------|
| `/start` | Show status and help |
| `/status` | Show queue status and config |
| `/setkey <key>` | Set Karakeep API key |
| `/flush` | Force-send all queued bookmarks immediately |

## 🛠 Architecture

```python
bot.py          # Main application (Python + python-telegram-bot)
├── /start      # Status + help message
├── /setkey     # Dynamic API key config
├── /status     # Queue stats
├── /flush      # Manual queue flush
├── Links       # → Karakeep POST /bookmarks
├── Text        # → Karakeep POST /bookmarks
├── Images      # → Karakeep via file URL
└── Queue       # SQLite-based, async retry loop
```

## 🔒 Security

- The bot **does not store your API key in any remote location** — only in memory + local `.env`
- Optional **chat ID allowlist** ensures only you can interact with the bot
- `.env` is gitignored — never commit your tokens
- The bot token is only visible in Docker container logs (masked on stdout)

## 📄 License

MIT — see [LICENSE](LICENSE)

---

Built by [Füvesi Magor](https://fuvesi.hu) · [GitHub](https://github.com/kalandor122)
