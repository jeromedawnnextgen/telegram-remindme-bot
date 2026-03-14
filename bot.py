import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import db
import scheduler as sched
from reminder_parser import parse_reminder

load_dotenv()

TOKEN = os.environ["TELEGRAM_TOKEN"]
TIMEZONE = os.environ.get("TIMEZONE", "UTC")
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reminder fire callback (called by APScheduler)
# ---------------------------------------------------------------------------

async def send_reminder(chat_id: int, task: str, reminder_id: int):
    await app.bot.send_message(chat_id=chat_id, text=f"⏰ Reminder: {task}")
    db.mark_fired(reminder_id)
    log.info("Fired reminder %d for chat %d", reminder_id, chat_id)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def is_allowed(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Hey! I'm your reminder buddy.\n\n"
        "Just tell me something like:\n"
        "  • remind me at 9:30 am to take out the trash\n"
        "  • remind me in 20 minutes to drink water\n"
        "  • remind me to call mom at 6pm\n\n"
        "Commands:\n"
        "  /list — see pending reminders\n"
        "  /cancel <id> — cancel a reminder"
    )


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    reminders = db.get_pending(chat_id)

    if not reminders:
        await update.message.reply_text("You have no pending reminders.")
        return

    tz = ZoneInfo(TIMEZONE)
    lines = []
    for r in reminders:
        fire_at = datetime.fromisoformat(r["fire_at"]).astimezone(tz)
        lines.append(f"[{r['id']}] {fire_at.strftime('%b %d %I:%M %p')} — {r['task']}")

    await update.message.reply_text("Pending reminders:\n" + "\n".join(lines))


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    args = ctx.args

    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /cancel <id>  (get IDs from /list)")
        return

    reminder_id = int(args[0])
    deleted = db.delete_reminder(reminder_id, chat_id)

    if deleted:
        sched.cancel_reminder(reminder_id)
        await update.message.reply_text(f"Reminder {reminder_id} cancelled.")
    else:
        await update.message.reply_text(f"No pending reminder found with ID {reminder_id}.")


# ---------------------------------------------------------------------------
# Free-text message handler
# ---------------------------------------------------------------------------

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    text = update.message.text or ""
    chat_id = update.effective_chat.id

    result = parse_reminder(text, TIMEZONE)

    if result is None:
        await update.message.reply_text(
            "I couldn't figure out a time from that. Try:\n"
            "  remind me at 9:30 am to take out the trash\n"
            "  remind me in 20 minutes to drink water"
        )
        return

    fire_at, task = result

    # Guard: reject if in the past
    now_utc = datetime.now(ZoneInfo("UTC"))
    if fire_at < now_utc:
        tz = ZoneInfo(TIMEZONE)
        fire_local = fire_at.astimezone(tz)
        await update.message.reply_text(
            f"That time ({fire_local.strftime('%I:%M %p')}) has already passed. "
            "Did you mean tomorrow? Try adding 'tomorrow' to your message."
        )
        return

    reminder_id = db.add_reminder(chat_id, task, fire_at)
    sched.schedule_reminder(reminder_id, chat_id, task, fire_at)

    tz = ZoneInfo(TIMEZONE)
    fire_local = fire_at.astimezone(tz)
    await update.message.reply_text(
        f"Got it! I'll remind you at {fire_local.strftime('%I:%M %p')} to: {task}"
    )
    log.info("Scheduled reminder %d: '%s' at %s", reminder_id, task, fire_at)


# ---------------------------------------------------------------------------
# Startup: restore pending reminders from DB
# ---------------------------------------------------------------------------

async def on_startup(application):
    db.init_db()
    sched.start(send_reminder)

    now_utc = datetime.now(ZoneInfo("UTC"))
    pending = db.get_all_pending()
    restored = 0

    for r in pending:
        fire_at = datetime.fromisoformat(r["fire_at"]).replace(tzinfo=ZoneInfo("UTC"))
        if fire_at > now_utc:
            sched.schedule_reminder(r["id"], r["chat_id"], r["task"], fire_at)
            restored += 1
        else:
            # Missed while bot was down — fire immediately
            sched.schedule_reminder(r["id"], r["chat_id"], r["task"], now_utc)
            restored += 1

    log.info("Restored %d pending reminders from DB", restored)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("list", cmd_list))
app.add_handler(CommandHandler("cancel", cmd_cancel))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == "__main__":
    log.info("Starting RemindMeBot...")
    app.run_polling(drop_pending_updates=True)
