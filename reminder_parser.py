import re
import dateparser
from datetime import datetime
from zoneinfo import ZoneInfo

# Strip leading filler before "remind me"
_FILLER = re.compile(
    r'^(no|hey|ok|okay|please|just|can you|could you)[,.\s]+',
    re.IGNORECASE,
)

# Matches common time expressions
_TIME_RE = re.compile(
    r'\b('
    r'in\s+\d+\s+(?:second|minute|hour|day|week)s?'
    r'|at\s+\d{1,4}(?::\d{2})?\s*(?:am|pm)?'
    r'|tomorrow(?:\s+at\s+\d{1,4}(?::\d{2})?\s*(?:am|pm)?)?'
    r'|next\s+\w+(?:\s+at\s+\d{1,4}(?::\d{2})?\s*(?:am|pm)?)?'
    r')',
    re.IGNORECASE,
)


def _normalize_time(s: str) -> str:
    """Convert bare digits like '841' → '8:41', '930' → '9:30'."""
    def fix(m):
        digits, suffix = m.group(1), m.group(2) or ""
        suffix = (" " + suffix) if suffix else ""
        if len(digits) == 3:
            return f"{digits[0]}:{digits[1:]}{suffix}"
        if len(digits) == 4:
            return f"{digits[:2]}:{digits[2:]}{suffix}"
        return m.group(0)
    return re.sub(r'\b(\d{3,4})\s*(am|pm)?\b', fix, s, flags=re.IGNORECASE)


def parse_reminder(text: str, timezone: str = "UTC") -> tuple[datetime, str] | None:
    text = _FILLER.sub("", text).strip()
    time_str, task = _extract(text)
    if not time_str:
        return None

    time_str = _normalize_time(time_str)
    task = task.strip() if task and task.strip() else "reminder"

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

    fire_at_utc = dt.astimezone(ZoneInfo("UTC"))
    now_utc = datetime.now(ZoneInfo("UTC"))
    if (fire_at_utc - now_utc).total_seconds() < -60:
        return None

    return fire_at_utc, task


def _extract(text: str) -> tuple[str | None, str | None]:
    # Pattern 1: "remind me at/in/on <time> to <task>"
    m = re.search(r'remind\s+me\s+(?:at|in|on)\s+(.+?)\s+to\s+(.+)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Pattern 2: "remind me to <task> at/in/on <time>"
    m = re.search(r'remind\s+me\s+to\s+(.+?)\s+(?:at|in|on)\s+(.+)', text, re.IGNORECASE)
    if m:
        return m.group(2).strip(), m.group(1).strip()

    # Fallback: find any time expression, treat the rest as the task
    m = _TIME_RE.search(text)
    if m:
        time_str = m.group(1)
        before = text[:m.start()].strip()
        after = text[m.end():].strip()
        # Clean "remind me [to]" out of whatever came before the time
        before = re.sub(r'\b(remind\s+me(\s+to)?)\b', '', before, flags=re.IGNORECASE).strip()
        # Clean leading "to" from whatever came after
        after = re.sub(r'^to\s+', '', after, flags=re.IGNORECASE).strip()
        task = (before + (" " if before and after else "") + after).strip()
        return time_str, task or None

    return None, None
