from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import datetime
from zoneinfo import ZoneInfo

scheduler = AsyncIOScheduler(timezone="UTC")


def start(send_reminder_fn):
    """Start the scheduler. Call once on bot startup."""
    scheduler._send_reminder = send_reminder_fn
    scheduler.start()


def schedule_reminder(reminder_id: int, chat_id: int, task: str, fire_at: datetime):
    """Schedule a one-shot job for a reminder."""
    scheduler.add_job(
        scheduler._send_reminder,
        trigger=DateTrigger(run_date=fire_at.astimezone(ZoneInfo("UTC"))),
        args=[chat_id, task, reminder_id],
        id=f"reminder_{reminder_id}",
        replace_existing=True,
        misfire_grace_time=300,  # fire up to 5 min late if bot was down
    )


def cancel_reminder(reminder_id: int):
    """Remove a scheduled job."""
    job_id = f"reminder_{reminder_id}"
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
