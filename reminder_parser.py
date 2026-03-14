import re
import dateparser
from datetime import datetime
from zoneinfo import ZoneInfo

# Patterns: "remind me at/in/on <time> to <task>"
#           "remind me to <task> at/in/on <time>"
#           bare: "at <time> <task>"
_PATTERNS = [
    re.compile(r"remind\s+me\s+(?:at|in|on)\s+(.+?)\s+to\s+(.+)", re.IGNORECASE),
    re.compile(r"remind\s+me\s+to\s+(.+?)\s+(?:at|in|on)\s+(.+)", re.IGNORECASE),
]


def parse_reminder(text: str, timezone: str = "UTC") -> tuple[datetime, str] | None:
    """
    Returns (fire_at_utc, task_text) or None if parsing fails.
    """
    time_str, task = _extract_time_and_task(text)
    if not time_str or not task:
        return None

    dt = dateparser.parse(
        time_str,
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if dt is None:
        return None

    # Convert to UTC
    fire_at_utc = dt.astimezone(ZoneInfo("UTC"))

    # Reject times in the past (more than 60s ago)
    now_utc = datetime.now(ZoneInfo("UTC"))
    if (fire_at_utc - now_utc).total_seconds() < -60:
        return None

    return fire_at_utc, task.strip()


def _extract_time_and_task(text: str) -> tuple[str | None, str | None]:
    # Pattern 1: "remind me at <time> to <task>"
    m = re.match(r"remind\s+me\s+(?:at|in|on)\s+(.+?)\s+to\s+(.+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Pattern 2: "remind me to <task> at/in/on <time>"
    m = re.match(r"remind\s+me\s+to\s+(.+?)\s+(?:at|in|on)\s+(.+)", text, re.IGNORECASE)
    if m:
        return m.group(2).strip(), m.group(1).strip()

    # Fallback: try to pull any time expression out of the full string
    # and treat the rest as the task
    m = re.search(
        r"(in\s+\d+\s+(?:minute|hour|day|week)s?|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|tomorrow(?:\s+at\s+.+)?|next\s+\w+(?:\s+at\s+.+)?)",
        text,
        re.IGNORECASE,
    )
    if m:
        time_str = m.group(1).strip()
        task = text[: m.start()].strip() + " " + text[m.end() :].strip()
        task = re.sub(r"\s+", " ", task).strip()
        return time_str, task or None

    return None, None
