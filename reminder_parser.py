import os
import re
import json
import logging
import dateparser
import google.generativeai as genai
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Strip leading filler before "remind me"
_FILLER = re.compile(
    r'^(no|hey|ok|okay|please|just|can you|could you)[,.\s]+',
    re.IGNORECASE,
)

# Time-of-day word group (used in multiple regexes)
_TOD = r'(?:\d{1,4}(?::\d{2})?(?:\s*(?:am|pm))?|noon|midnight|morning|evening|night)'

# Full time expression (one-off)
_TIME_EXPR_RE = re.compile(
    r'\b('
    r'in\s+\d+\s+(?:second|minute|hour|day|week)s?'
    r'|(?:tomorrow|today)(?:\s+at\s+' + _TOD + r')?'
    r'|next\s+\w+(?:\s+at\s+' + _TOD + r')?'
    r'|at\s+' + _TOD + r'(?:\s+(?:tomorrow|today|next\s+\w+))?'
    r'|noon|midnight'
    r')',
    re.IGNORECASE,
)

# Recurring pattern: "every day at 4 PM", "every Monday at 8am"
_RECURRING_RE = re.compile(
    r'\b(every\s+(?:day|morning|evening|night|weekday|weekend|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
    r'(?:\s+at\s+' + _TOD + r')?)',
    re.IGNORECASE,
)

_gemini: genai.GenerativeModel | None = None


def _get_gemini() -> genai.GenerativeModel | None:
    global _gemini
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    if _gemini is None:
        genai.configure(api_key=api_key)
        _gemini = genai.GenerativeModel("gemini-2.0-flash")
    return _gemini


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


def _validate_future(fire_at: datetime) -> datetime | None:
    now_utc = datetime.now(ZoneInfo("UTC"))
    fire_at_utc = fire_at.astimezone(ZoneInfo("UTC"))
    if (fire_at_utc - now_utc).total_seconds() < -60:
        return None
    return fire_at_utc


def _clean_task(text: str) -> str:
    text = re.sub(r'\b(remind\s+me(\s+to)?|please|just)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^(to|and|,)\s+', '', text, flags=re.IGNORECASE).strip()
    return text or "reminder"


def _extract(text: str) -> tuple[str | None, str | None, bool]:
    """Returns (time_str, task, is_recurring)."""

    # Recurring takes priority
    rec_m = _RECURRING_RE.search(text)
    if rec_m:
        time_str = rec_m.group(1)
        task_raw = text[:rec_m.start()] + text[rec_m.end():]
        return time_str, _clean_task(task_raw), True

    # "remind me [at|in|on|tomorrow|today|next X] ... to <task>"
    m = re.search(
        r'remind\s+me\s+((?:at|in|on|tomorrow|today|next\s+\w+).+?)\s+to\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip(), False

    # "remind me to <task> [at|in|on] <time>"
    m = re.search(
        r'remind\s+me\s+to\s+(.+?)\s+((?:at|in|on)\s+.+)',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(2).strip(), m.group(1).strip(), False

    # Fallback: find any time expression, treat the rest as task
    time_m = _TIME_EXPR_RE.search(text)
    if time_m:
        time_str = time_m.group(1)
        before = text[:time_m.start()].strip()
        after = text[time_m.end():].strip()
        task = _clean_task(before + " " + after)
        return time_str, task, False

    return None, None, False


# ---------------------------------------------------------------------------
# Recurrence helpers
# ---------------------------------------------------------------------------

_DAY_MAP = {
    'monday': 'mon', 'tuesday': 'tue', 'wednesday': 'wed',
    'thursday': 'thu', 'friday': 'fri', 'saturday': 'sat', 'sunday': 'sun',
}
_DEFAULT_HOURS = {'morning': 8, 'evening': 18, 'night': 21, 'day': 9}


def _parse_recurrence(time_str: str, timezone: str) -> dict | None:
    """Parse 'every day at 4 PM' into a recurrence config dict."""
    m = re.match(
        r'every\s+(day|morning|evening|night|weekday|weekend|'
        r'monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
        r'(?:\s+at\s+(.+))?',
        time_str, re.IGNORECASE,
    )
    if not m:
        return None

    period = m.group(1).lower()
    time_part = m.group(2)

    hour = _DEFAULT_HOURS.get(period, 9)
    minute = 0

    if time_part:
        time_part = _normalize_time(time_part.strip())
        dt = dateparser.parse(
            time_part,
            settings={"TIMEZONE": timezone, "RETURN_AS_TIMEZONE_AWARE": True},
        )
        if dt:
            local_dt = dt.astimezone(ZoneInfo(timezone))
            hour, minute = local_dt.hour, local_dt.minute

    # Determine day_of_week cron expression
    if period in _DAY_MAP:
        day_of_week = _DAY_MAP[period]
    elif period == 'weekday':
        day_of_week = 'mon-fri'
    elif period == 'weekend':
        day_of_week = 'sat,sun'
    else:
        day_of_week = None  # every day / morning / evening / night

    # Human-readable label
    display_hour = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    time_label = f"{display_hour}:{minute:02d} {ampm}"
    if period in ('morning', 'evening', 'night'):
        label = f"every {period} at {time_label}"
    elif period in _DAY_MAP:
        label = f"every {period.capitalize()} at {time_label}"
    elif period == 'weekday':
        label = f"every weekday at {time_label}"
    elif period == 'weekend':
        label = f"every weekend at {time_label}"
    else:
        label = f"every day at {time_label}"

    return {
        "hour": hour,
        "minute": minute,
        "day_of_week": day_of_week,
        "label": label,
        "timezone": timezone,
    }


def next_cron_occurrence(recurrence: dict, after: datetime) -> datetime:
    """Return the next datetime matching the recurrence config, after `after`."""
    tz = ZoneInfo(recurrence.get("timezone", "UTC"))
    now = after.astimezone(tz)
    hour = recurrence["hour"]
    minute = recurrence["minute"]
    day_of_week = recurrence.get("day_of_week")

    # Build set of valid weekday indices (Mon=0 … Sun=6)
    valid_weekdays: set[int] | None = None
    if day_of_week:
        wmap = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
        if '-' in day_of_week:
            start, end = day_of_week.split('-')
            valid_weekdays = set(range(wmap[start], wmap[end] + 1))
        elif ',' in day_of_week:
            valid_weekdays = {wmap[d.strip()] for d in day_of_week.split(',')}
        else:
            valid_weekdays = {wmap[day_of_week]}

    candidate_day = now.date()
    for _ in range(8):
        candidate = datetime(
            candidate_day.year, candidate_day.month, candidate_day.day,
            hour, minute, 0, tzinfo=tz,
        )
        if candidate > now and (valid_weekdays is None or candidate.weekday() in valid_weekdays):
            return candidate
        candidate_day = candidate_day + timedelta(days=1)

    # Fallback: tomorrow at the specified time
    tomorrow = now.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, 0, tzinfo=tz)


def format_time_label(fire_at: datetime, timezone: str) -> str:
    """Human-readable confirmation label for a one-off fire time."""
    tz = ZoneInfo(timezone)
    local = fire_at.astimezone(tz)
    now_local = datetime.now(tz)
    delta_s = (fire_at.astimezone(ZoneInfo("UTC")) - datetime.now(ZoneInfo("UTC"))).total_seconds()

    if delta_s < 3600:
        mins = max(1, int(delta_s / 60))
        return f"in {mins} minute{'s' if mins != 1 else ''}"

    hour = int(local.strftime("%I"))  # 1–12, no leading zero
    time_str = f"{hour}:{local.strftime('%M %p')}"

    if local.date() == now_local.date():
        return f"today at {time_str}"
    if local.date() == now_local.date() + timedelta(days=1):
        return f"tomorrow at {time_str}"
    return f"{local.strftime('%b %d')} at {time_str}"


# ---------------------------------------------------------------------------
# Regex-based parser (fast, free)
# ---------------------------------------------------------------------------

def parse_reminder(text: str, timezone: str = "UTC") -> tuple[datetime, str, dict | None] | None:
    """Returns (fire_at_utc, task, recurrence_dict | None) or None."""
    text = _FILLER.sub("", text).strip()
    time_str, task, is_recurring = _extract(text)
    if not time_str:
        return None

    time_str = _normalize_time(time_str)
    task = (task or "reminder").strip() or "reminder"

    if is_recurring:
        recurrence = _parse_recurrence(time_str, timezone)
        if recurrence is None:
            return None
        fire_at = next_cron_occurrence(recurrence, datetime.now(ZoneInfo(timezone)))
        return fire_at.astimezone(ZoneInfo("UTC")), task, recurrence

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

    fire_at_utc = _validate_future(dt)
    return (fire_at_utc, task, None) if fire_at_utc else None


# ---------------------------------------------------------------------------
# Claude fallback (handles anything the regex misses)
# ---------------------------------------------------------------------------

async def _gemini_fallback(text: str, timezone: str) -> tuple[datetime, str, dict | None] | None:
    model = _get_gemini()
    if model is None:
        log.warning("Gemini fallback skipped: GEMINI_API_KEY not set")
        return None
    log.info("Trying Gemini fallback for: %r", text)

    now = datetime.now(ZoneInfo(timezone))
    prompt = (
        f"Extract the reminder time and task from this message.\n"
        f"Current time: {now.strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"Timezone: {timezone}\n"
        f'Message: "{text}"\n\n'
        "Reply with ONLY valid JSON:\n"
        '{"fire_at_iso": "<ISO 8601 with offset, e.g. 2026-03-13T21:30:00-05:00>", "task": "<what to remind>"}\n\n'
        "If no clear time is found:\n"
        '{"error": "no time found"}\n\n'
        "Rules:\n"
        "- fire_at_iso must be in the future\n"
        "- Include the UTC offset for the given timezone\n"
        "- Prefer PM if ambiguous and before 8am (e.g. 'at 6' → 6 PM)\n"
        "- If time has passed today, assume tomorrow"
    )

    try:
        response = await model.generate_content_async(prompt)
        raw = response.text.strip()

        log.info("Gemini raw response: %r", raw)
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.DOTALL).strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            log.warning("No JSON found in Gemini response: %r", raw)
            return None

        data = json.loads(m.group())
        if "error" in data:
            return None

        fire_at = datetime.fromisoformat(data["fire_at_iso"])
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=ZoneInfo(timezone))

        fire_at_utc = _validate_future(fire_at)
        if fire_at_utc is None:
            return None

        return fire_at_utc, data["task"].strip(), None

    except Exception as e:
        log.error("Gemini fallback failed: %s", e, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Main entry point used by bot.py
# ---------------------------------------------------------------------------

async def parse_reminder_smart(
    text: str, timezone: str = "UTC"
) -> tuple[datetime, str, dict | None] | None:
    """Try regex first; fall back to Claude if it can't parse.

    Returns (fire_at_utc, task, recurrence) or None.
    recurrence is None for one-off, dict with hour/minute/day_of_week/label/timezone for recurring.
    """
    result = parse_reminder(text, timezone)
    if result is not None:
        return result
    return await _gemini_fallback(text, timezone)
