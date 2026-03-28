import json
import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import db
import scheduler as sched
from reminder_parser import parse_reminder_smart, format_time_label, next_cron_occurrence

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

    reminder = db.get_reminder(reminder_id)
    if reminder and reminder.get("recurrence"):
        # Recurring: update next fire_at in DB; APScheduler CronTrigger handles rescheduling
        recurrence = json.loads(reminder["recurrence"])
        now = datetime.now(ZoneInfo(recurrence.get("timezone", TIMEZONE)))
        next_fire = next_cron_occurrence(recurrence, now + timedelta(minutes=1))
        db.update_reminder_fire_at(reminder_id, next_fire.astimezone(ZoneInfo("UTC")))
    else:
        db.mark_fired(reminder_id)

    log.info("Fired reminder %d for chat %d", reminder_id, chat_id)


# ---------------------------------------------------------------------------
# Daily brief callback (called by APScheduler)
# ---------------------------------------------------------------------------

async def send_daily_brief(chat_id: int):
    tz = ZoneInfo(TIMEZONE)
    now_local = datetime.now(tz)

    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))
    today_end = today_start + timedelta(days=1)
    yesterday_str = (now_local.date() - timedelta(days=1)).isoformat()

    open_tasks = db.get_tasks(chat_id, include_done=False)
    todays_reminders = db.get_todays_reminders(chat_id, today_start, today_end)
    completed_yesterday = db.count_completed_yesterday(chat_id, yesterday_str)

    lines = [f"Good morning! Here's your brief for {now_local.strftime('%A, %b %d')}:\n"]

    if open_tasks:
        lines.append(f"Open tasks ({len(open_tasks)}):")
        for i, t in enumerate(open_tasks, 1):
            proj = f" [{t['project']}]" if t.get("project") else ""
            lines.append(f"  {i}. {t['task']}{proj}")
    else:
        lines.append("No open tasks.")

    lines.append("")

    if todays_reminders:
        lines.append(f"Today's reminders ({len(todays_reminders)}):")
        for r in todays_reminders:
            fire_at = datetime.fromisoformat(r["fire_at"]).astimezone(tz)
            hour = int(fire_at.strftime("%I"))
            lines.append(f"  • {hour}:{fire_at.strftime('%M %p')} — {r['task']}")
    else:
        lines.append("No reminders scheduled for today.")

    lines.append("")
    lines.append(f"Completed yesterday: {completed_yesterday} task{'s' if completed_yesterday != 1 else ''}")

    await app.bot.send_message(chat_id=chat_id, text="\n".join(lines))
    log.info("Sent daily brief to chat %d", chat_id)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def is_allowed(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


# ---------------------------------------------------------------------------
# Existing command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Hey! I'm your personal assistant bot.\n\n"
        "REMINDERS\n"
        "  remind me at 9:30 am to take out the trash\n"
        "  remind me in 20 minutes to drink water\n"
        "  remind me to call mom tomorrow at 6pm\n"
        "  remind me every day at 4 PM to exercise\n"
        "  /list — pending reminders\n"
        "  /cancel <id> — cancel a reminder\n\n"
        "TASKS\n"
        "  Add task: buy milk\n"
        "  Add task to groceries: buy milk\n"
        "  Tasks — show open tasks\n"
        "  Done 2 — complete task #2\n"
        "  Done buy milk — complete by name\n"
        "  Show groceries tasks — filter by project\n"
        "  Clear completed\n\n"
        "NOTES\n"
        "  Note: anything you want to capture\n"
        "  Notes — show last 10 notes\n\n"
        "DAILY BRIEF\n"
        "  /brief — show your brief right now\n"
        "  (auto-sent every morning at 7:00 AM)"
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
        hour = int(fire_at.strftime("%I"))
        time_str = f"{hour}:{fire_at.strftime('%M %p')}"
        recurring_tag = " (recurring)" if r.get("recurrence") else ""
        lines.append(f"[{r['id']}] {fire_at.strftime('%b %d')} {time_str}{recurring_tag} — {r['task']}")

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


async def cmd_brief(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await send_daily_brief(update.effective_chat.id)


# ---------------------------------------------------------------------------
# Task handlers
# ---------------------------------------------------------------------------

async def handle_task_add(update: Update, text: str):
    chat_id = update.effective_chat.id

    # "Add task to <project>: <task>"
    m = re.search(r'add\s+task\s+to\s+(.+?):\s*(.+)', text, re.IGNORECASE)
    if m:
        project = m.group(1).strip()
        task = m.group(2).strip()
        db.add_task(chat_id, task, project)
        await update.message.reply_text(f"Task added to [{project}]: {task}")
        return

    # "Add task: <task>"
    m = re.search(r'add\s+task:\s*(.+)', text, re.IGNORECASE)
    if m:
        task = m.group(1).strip()
        db.add_task(chat_id, task)
        await update.message.reply_text(f"Task added: {task}")
        return

    await update.message.reply_text("Usage: Add task: <description>  or  Add task to <project>: <description>")


async def handle_tasks_show(update: Update, project: str | None = None):
    chat_id = update.effective_chat.id
    tasks = db.get_tasks(chat_id, project=project, include_done=False)

    if not tasks:
        header = f"No open tasks in [{project}]." if project else "No open tasks."
        await update.message.reply_text(header)
        return

    header = f"Open tasks in [{project}]:" if project else "Open tasks:"
    lines = [header]
    for i, t in enumerate(tasks, 1):
        proj_tag = f" [{t['project']}]" if t.get("project") and not project else ""
        lines.append(f"  {i}. {t['task']}{proj_tag}")
    await update.message.reply_text("\n".join(lines))


async def handle_task_done(update: Update, arg: str):
    chat_id = update.effective_chat.id

    if arg.isdigit():
        # Done by position in current open task list
        tasks = db.get_tasks(chat_id, include_done=False)
        idx = int(arg) - 1
        if 0 <= idx < len(tasks):
            db.complete_task(tasks[idx]["id"], chat_id)
            await update.message.reply_text(f"Done: {tasks[idx]['task']}")
        else:
            await update.message.reply_text(f"No task #{arg}. Use 'Tasks' to see the list.")
    else:
        ok = db.complete_task_by_name(chat_id, arg)
        if ok:
            await update.message.reply_text(f"Done: {arg}")
        else:
            await update.message.reply_text(f"No open task matching '{arg}'.")


async def handle_clear_completed(update: Update):
    chat_id = update.effective_chat.id
    count = db.clear_completed_tasks(chat_id)
    await update.message.reply_text(
        f"Cleared {count} completed task{'s' if count != 1 else ''}."
    )


# ---------------------------------------------------------------------------
# Note handlers
# ---------------------------------------------------------------------------

async def handle_note_add(update: Update, text: str):
    chat_id = update.effective_chat.id
    m = re.search(r'note[:\s]+(.+)', text, re.IGNORECASE | re.DOTALL)
    if not m:
        await update.message.reply_text("Usage: Note: <anything>")
        return
    note = m.group(1).strip()
    db.add_note(chat_id, note)
    await update.message.reply_text(f"Note saved: {note}")


async def handle_notes_show(update: Update):
    chat_id = update.effective_chat.id
    notes = db.get_notes(chat_id, limit=10)
    if not notes:
        await update.message.reply_text("No notes yet.")
        return
    tz = ZoneInfo(TIMEZONE)
    lines = ["Last 10 notes:"]
    for n in notes:
        ts = datetime.fromisoformat(n["created_at"]).astimezone(tz)
        lines.append(f"  [{ts.strftime('%b %d %H:%M')}] {n['note']}")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Free-text message handler (routing + reminders)
# ---------------------------------------------------------------------------

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    text = (update.message.text or "").strip()
    tl = text.lower()

    # --- Notes ---
    if re.match(r'note[:\s]', tl, re.IGNORECASE):
        await handle_note_add(update, text)
        return
    if tl in ("notes", "show notes"):
        await handle_notes_show(update)
        return

    # --- Tasks ---
    if re.match(r'add\s+task', tl, re.IGNORECASE):
        await handle_task_add(update, text)
        return
    if tl in ("tasks", "show tasks", "my tasks"):
        await handle_tasks_show(update)
        return
    if re.match(r'done\s+\S', tl):
        arg = re.sub(r'^done\s+', '', text, flags=re.IGNORECASE).strip()
        await handle_task_done(update, arg)
        return
    if tl == "clear completed":
        await handle_clear_completed(update)
        return
    # "Show <project> tasks"
    m = re.match(r'show\s+(.+?)\s+tasks?$', tl, re.IGNORECASE)
    if m:
        await handle_tasks_show(update, project=m.group(1).strip())
        return

    # --- Reminders (existing flow) ---
    result = await parse_reminder_smart(text, TIMEZONE)

    if result is None:
        await update.message.reply_text(
            "I couldn't figure out a time from that. Try:\n"
            "  remind me at 9:30 am to take out the trash\n"
            "  remind me in 20 minutes to drink water\n"
            "  remind me every day at 4 PM to exercise\n\n"
            "Or use /start to see all commands."
        )
        return

    fire_at, task, recurrence = result

    # Guard: reject past times for one-off reminders
    if not recurrence:
        now_utc = datetime.now(ZoneInfo("UTC"))
        if fire_at < now_utc:
            tz = ZoneInfo(TIMEZONE)
            fire_local = fire_at.astimezone(tz)
            hour = int(fire_local.strftime("%I"))
            await update.message.reply_text(
                f"That time ({hour}:{fire_local.strftime('%M %p')}) has already passed. "
                "Did you mean tomorrow? Try adding 'tomorrow' to your message."
            )
            return

    recurrence_json = json.dumps(recurrence) if recurrence else None
    reminder_id = db.add_reminder(update.effective_chat.id, task, fire_at, recurrence_json)

    if recurrence:
        sched.schedule_recurring_reminder(reminder_id, update.effective_chat.id, task, recurrence)
        time_label = recurrence["label"]
    else:
        sched.schedule_reminder(reminder_id, update.effective_chat.id, task, fire_at)
        time_label = format_time_label(fire_at, TIMEZONE)

    await update.message.reply_text(f"Got it! Reminding you {time_label} to: {task}")
    log.info("Scheduled reminder %d: '%s' at %s (recurring=%s)", reminder_id, task, fire_at, bool(recurrence))


# ---------------------------------------------------------------------------
# Startup: restore pending reminders + schedule daily brief
# ---------------------------------------------------------------------------

async def on_startup(application):
    db.init_db()
    sched.start(send_reminder, send_daily_brief)

    # Schedule daily brief at 7:00 AM user's local time
    sched.schedule_daily_brief(ALLOWED_CHAT_ID, hour=7, minute=0, timezone=TIMEZONE)

    now_utc = datetime.now(ZoneInfo("UTC"))
    pending = db.get_all_pending()
    restored = 0

    for r in pending:
        fire_at = datetime.fromisoformat(r["fire_at"]).replace(tzinfo=ZoneInfo("UTC"))
        recurrence_json = r.get("recurrence")

        if recurrence_json:
            recurrence = json.loads(recurrence_json)
            sched.schedule_recurring_reminder(r["id"], r["chat_id"], r["task"], recurrence)
            restored += 1
        elif fire_at > now_utc:
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
app.add_handler(CommandHandler("brief", cmd_brief))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == "__main__":
    log.info("Starting RemindMeBot...")
    app.run_polling(drop_pending_updates=True)
