#!/usr/bin/env python3
"""
Karakeep Telegram Bot — middleware with offline queue.
Accepts links, text, and images via Telegram, forwards to Karakeep API.
If Karakeep is offline, queues locally in SQLite and retries periodically.
"""

import os
import json
import sqlite3
import asyncio
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ─── Configuration ───────────────────────────────────────────────────────────

KARAKEEP_URL = os.environ.get("KARAKEEP_URL", "").rstrip("/")
KARAKEEP_TOKEN = os.environ.get("KARAKEEP_TOKEN", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALLOWED_USER_IDS = [
    int(x.strip()) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
]
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "queue.db"
RETRY_INTERVAL_SECONDS = int(os.environ.get("RETRY_INTERVAL", "60"))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
)
log = logging.getLogger("karakeepbot")

# ─── Database ────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            caption TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            retries INTEGER DEFAULT 0,
            last_error TEXT DEFAULT ''
        )
    """)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()

def enqueue(type_: str, content: str, caption: str = ""):
    with get_db() as db:
        db.execute(
            "INSERT INTO queue (type, content, caption, created_at) VALUES (?, ?, ?, ?)",
            (type_, content, caption, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()

def dequeue_all():
    with get_db() as db:
        rows = db.execute("SELECT * FROM queue ORDER BY id ASC").fetchall()
        items = [dict(r) for r in rows]
        db.execute("DELETE FROM queue")
        db.commit()
    return items

def requeue_failed(items: list[dict]):
    with get_db() as db:
        for item in items:
            db.execute(
                "INSERT INTO queue (type, content, caption, retries, last_error) VALUES (?, ?, ?, ?, ?)",
                (item["type"], item["content"], item.get("caption", ""),
                 item.get("retries", 0) + 1, item.get("last_error", "")),
            )
        db.commit()

def queue_count() -> int:
    with get_db() as db:
        return db.execute("SELECT COUNT(*) as c FROM queue").fetchone()["c"]

# ─── Karakeep API ────────────────────────────────────────────────────────────

async def send_to_karakeep(type_: str, content: str, caption: str = "") -> tuple[bool, str]:
    if not KARAKEEP_TOKEN:
        return False, "Karakeep API key is not set — use /setkey [api_key]"

    headers = {
        "Authorization": f"Bearer {KARAKEEP_TOKEN}",
        "Content-Type": "application/json",
    }

    if type_ == "link":
        payload = {"type": "link", "url": content}
    elif type_ == "text":
        payload = {"type": "text", "text": content}
    elif type_ == "image":
        payload = {"type": "link", "url": content}
    else:
        return False, f"Unknown type: {type_}"

    if caption:
        payload.setdefault("title", caption[:200])

    try:
        log.info(f"Sending: type={type_}, preview={content[:80]}...")
        r = await asyncio.to_thread(
            requests.post,
            f"{KARAKEEP_URL}/api/v1/bookmarks",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201):
            return True, "✅ Saved to Karakeep!"
        elif r.status_code == 401:
            return False, "❌ Invalid API key"
        elif r.status_code in (400, 422):
            # Retry without title for compatibility
            simple = {"type": "link", "url": content}
            r2 = await asyncio.to_thread(
                requests.post, f"{KARAKEEP_URL}/api/v1/bookmarks",
                headers=headers, json=simple, timeout=30,
            )
            if r2.status_code in (200, 201):
                return True, "✅ Saved!"
            return False, f"❌ Karakeep error ({r.status_code}): {r.text[:200]}"
        else:
            return False, f"❌ Karakeep error ({r.status_code}): {r.text[:200]}"
    except requests.ConnectionError:
        return False, "🔌 Karakeep is unreachable"
    except requests.Timeout:
        return False, "⏱️ Karakeep timed out"
    except Exception as e:
        return False, f"❌ Error: {e}"

# ─── Periodic queue flush ────────────────────────────────────────────────────

_background_tasks = set()

async def flush_queue():
    if not KARAKEEP_TOKEN:
        return
    pending = dequeue_all()
    if not pending:
        return
    log.info(f"Flushing queue: {len(pending)} items")
    failed = []
    for item in pending:
        ok, msg = await send_to_karakeep(item["type"], item["content"], item.get("caption", ""))
        if not ok:
            item["last_error"] = msg
            failed.append(item)
        await asyncio.sleep(0.5)
    if failed:
        requeue_failed(failed)
        log.info(f"Re-queued {len(failed)} items after failure")
    else:
        log.info(f"Queue flushed ({len(pending)} items)")

async def flush_loop(stop_event: asyncio.Event):
    """Periodically flush queue until stop_event is set."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().create_task(flush_queue()),
                timeout=30,
            )
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            log.error(f"Queue flush error: {e}")
        # Wait for retry interval or until cancelled
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RETRY_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass  # Normal timeout, just loop again

# ─── Telegram handlers ───────────────────────────────────────────────────────

def is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return update.effective_user and update.effective_user.id in ALLOWED_USER_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("⛔ You are not authorized.")
        return
    q = queue_count()
    parts = []
    if KARAKEEP_TOKEN:
        parts.append("✅ API key configured")
    else:
        parts.append("⚠️ No API key — use /setkey [api_key]")
    if q > 0:
        parts.append(f"⏳ {q} items queued")

    await update.message.reply_text(
        "🤖 *Karakeep Bot*\n\n"
        "Send me a link, text, or image and I'll save it to your Karakeep instance!\n\n"
        "*Status:*\n• " + "\n• ".join(parts) + "\n\n"
        "*Commands:*\n"
        "• `/setkey [api_key]` — Set Karakeep API key\n"
        "• `/status` — Show queue status\n"
        "• `/flush` — Send queued items immediately",
        parse_mode="Markdown",
    )

async def setkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    global KARAKEEP_TOKEN
    if not context.args:
        await update.message.reply_text("Usage: `/setkey [api_key]`", parse_mode="Markdown")
        return
    KARAKEEP_TOKEN = context.args[0]
    log.info("API key updated")
    await update.message.reply_text("✅ API key saved!")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    q = queue_count()
    lines = [
        "📊 *Status*",
        f"• Queue: *{q}* item{'s' if q != 1 else ''}",
        f"• Karakeep URL: `{KARAKEEP_URL}`",
        f"• API key: {'✅ set' if KARAKEEP_TOKEN else '❌ not set'}",
    ]
    if q > 0:
        lines.append("\n`/flush` to send now")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def flush_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    q = queue_count()
    if q == 0:
        await update.message.reply_text("✅ Queue is empty.")
        return
    await update.message.reply_text(f"⏳ Sending {q} item{'s' if q != 1 else ''}...")
    await flush_queue()
    remaining = queue_count()
    if remaining == 0:
        await update.message.reply_text("✅ Queue flushed! 🎉")
    else:
        await update.message.reply_text(
            f"⚠️ {remaining} item{'s' if remaining != 1 else ''} failed — will retry automatically..."
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    msg = update.message
    if not msg:
        return
    text = msg.text or msg.caption or ""

    # Determine content type from message
    content_type = None
    content_value = ""
    caption = text

    if msg.photo:
        photo = msg.photo[-1]
        file = await photo.get_file()
        content_type = "image"
        content_value = file.file_path
        caption = msg.caption or ""
    elif msg.entities or msg.caption_entities:
        urls = [e for e in (msg.entities or []) if e.type == "url"]
        cap_urls = [e for e in (msg.caption_entities or []) if e.type == "url"]
        all_urls = urls + cap_urls
        if all_urls:
            e = all_urls[0]
            content_type = "link"
            content_value = text[e.offset:e.offset + e.length]
            caption = text.replace(content_value, "").strip()
    elif text:
        content_type = "text"
        content_value = text

    if not content_type:
        await msg.reply_text("Send me a link, text, or image!")
        return

    log.info(f"Incoming: type={content_type}, preview={content_value[:80]}...")

    ok, result_msg = await send_to_karakeep(content_type, content_value, caption)
    if ok:
        await msg.reply_text(result_msg)
    else:
        enqueue(content_type, content_value, caption)
        q = queue_count()
        await msg.reply_text(
            f"⏳ *Queued* (#{q})\n{result_msg}\n"
            f"Will retry automatically when Karakeep is back online.",
            parse_mode="Markdown",
        )

# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN environment variable is not set!")
        return

    # Create stop event for clean shutdown
    stop_event = asyncio.Event()

    # Start periodic queue flush as a background task
    flush_task = asyncio.create_task(flush_loop(stop_event))
    _background_tasks.add(flush_task)
    flush_task.add_done_callback(_background_tasks.discard)
    log.info(f"Queue flush worker started (interval={RETRY_INTERVAL_SECONDS}s)")

    # Build bot app
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setkey", setkey))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("flush", flush_cmd))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    log.info(f"Bot starting! URL={KARAKEEP_URL}, API key={'✅' if KARAKEEP_TOKEN else '❌'}")

    # Run manually (not via run_polling) so we can run background tasks
    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        log.info("Bot is running. Press Ctrl+C to stop.")

        # Wait until stop signal
        stop_signal = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_event_loop().add_signal_handler(
                    sig, lambda: asyncio.create_task(shutdown(app, stop_event, stop_signal))
                )
            except NotImplementedError:
                pass  # Windows doesn't support add_signal_handler

        await stop_signal.wait()
    finally:
        await shutdown(app, stop_event, None)

async def shutdown(app, stop_event, stop_signal):
    """Graceful shutdown."""
    log.info("Shutting down...")
    stop_event.set()

    try:
        if app.updater:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception as e:
        log.warning(f"Shutdown error: {e}")

    if stop_signal:
        stop_signal.set()

    log.info("Goodbye!")

if __name__ == "__main__":
    asyncio.run(main())
