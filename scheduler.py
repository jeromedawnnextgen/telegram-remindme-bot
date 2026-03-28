from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from zoneinfo import ZoneInfo

scheduler = AsyncIOScheduler(timezone="UTC")


def start(send_reminder_fn, send_daily_brief_fn=None):
    """Start the scheduler. Call once on bot startup."""
    scheduler._send_reminder = send_reminder_fn
    if send_daily_brief_fn:
        scheduler._send_daily_brief = send_daily_brief_fn
    scheduler.start()


def schedule_reminder(reminder_id: int, chat_id: int, task: str, fire_at: datetime):
    """Schedule a one-shot job for a reminder."""
    scheduler.add_job(
        scheduler._send_reminder,
        trigger=DateTrigger(run_date=fire_at.astimezone(ZoneInfo("UTC"))),
        args=[chat_id, task, reminder_id],
        id=f"reminder_{reminder_id}",
        replace_existing=True,
        misfire_grace_time=300,
    )


def schedule_recurring_reminder(reminder_id: int, chat_id: int, task: str, recurrence: dict):
    """Schedule a recurring job using CronTrigger.

    recurrence dict: {hour, minute, day_of_week (optional), timezone}
    """
    timezone = recurrence.get("timezone", "UTC")
    cron_kwargs: dict = {
        "hour": recurrence["hour"],
        "minute": recurrence["minute"],
        "timezone": timezone,
    }
    day_of_week = recurrence.get("day_of_week")
    if day_of_week:
        cron_kwargs["day_of_week"] = day_of_week

    scheduler.add_job(
        scheduler._send_reminder,
        trigger=CronTrigger(**cron_kwargs),
        args=[chat_id, task, reminder_id],
        id=f"reminder_{reminder_id}",
        replace_existing=True,
    )


def schedule_daily_brief(chat_id: int, hour: int, minute: int, timezone: str):
    """Schedule the daily brief at a fixed local time."""
    scheduler.add_job(
        scheduler._send_daily_brief,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone),
        args=[chat_id],
        id=f"daily_brief_{chat_id}",
        replace_existing=True,
    )


def cancel_reminder(reminder_id: int):
    """Remove a scheduled job."""
    job_id = f"reminder_{reminder_id}"
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
