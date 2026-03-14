import os
import re
import json
import dateparser
import anthropic
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

_claude: anthropic.AsyncAnthropic | None = None


def _get_claude() -> anthropic.AsyncAnthropic | None:
    global _claude
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if _claude is None:
        _claude = anthropic.AsyncAnthropic(api_key=api_key)
    return _claude


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
    """Return fire_at if it's in the future, else None."""
    now_utc = datetime.now(ZoneInfo("UTC"))
    fire_at_utc = fire_at.astimezone(ZoneInfo("UTC"))
    if (fire_at_utc - now_utc).total_seconds() < -60:
        return None
    return fire_at_utc


# ---------------------------------------------------------------------------
# Regex-based parser (fast, free)
# ---------------------------------------------------------------------------

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

    fire_at_utc = _validate_future(dt)
    return (fire_at_utc, task) if fire_at_utc else None


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
        before = re.sub(r'\b(remind\s+me(\s+to)?)\b', '', before, flags=re.IGNORECASE).strip()
        after = re.sub(r'^to\s+', '', after, flags=re.IGNORECASE).strip()
        task = (before + (" " if before and after else "") + after).strip()
        return time_str, task or None

    return None, None


# ---------------------------------------------------------------------------
# Claude fallback (handles anything the regex misses)
# ---------------------------------------------------------------------------

async def _claude_fallback(text: str, timezone: str) -> tuple[datetime, str] | None:
    client = _get_claude()
    if client is None:
        return None

    now = datetime.now(ZoneInfo(timezone))

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": (
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
            }]
        )

        data = json.loads(response.content[0].text.strip())
        if "error" in data:
            return None

        fire_at = datetime.fromisoformat(data["fire_at_iso"])
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=ZoneInfo(timezone))

        fire_at_utc = _validate_future(fire_at)
        if fire_at_utc is None:
            return None

        return fire_at_utc, data["task"].strip()

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point used by bot.py
# ---------------------------------------------------------------------------

async def parse_reminder_smart(text: str, timezone: str = "UTC") -> tuple[datetime, str] | None:
    """Try regex first; fall back to Claude if it can't parse."""
    result = parse_reminder(text, timezone)
    if result is not None:
        return result
    return await _claude_fallback(text, timezone)
