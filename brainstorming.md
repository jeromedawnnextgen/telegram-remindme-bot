# RemindMeBot — Brainstorming & Iteration Plan

## Core Idea
A personal Telegram buddy that understands natural language reminders and fires a message back at the right time.

> "remind me at 9:30 am to take out the trash"
> → Bot replies: "Got it! I'll remind you at 9:30 AM to take out the trash."
> → At 9:30 AM: "🗑️ Take out the trash!"

---

## Architecture Options

### Option A — Simple (Python + APScheduler, runs locally or on a VPS)
```
Telegram → Polling loop → Parse message → Store reminder in SQLite → APScheduler fires → Send message
```
- **Pros:** Zero cloud cost, easy to debug locally, no external scheduler needed
- **Cons:** Process must stay alive; if it crashes, reminders are lost unless you persist + reload on startup
- **Best for:** Getting v1 running fast

### Option B — Azure Functions (matches your existing QUICKSTART)
```
Telegram → Webhook (HTTP trigger) → Parse + store in Azure Table Storage/Cosmos
Azure Timer Trigger (every minute) → Check due reminders → Send message
```
- **Pros:** Serverless, scales, always on, fits your existing Azure knowledge
- **Cons:** Timer trigger fires at most every minute (slight delay), small cold-start latency
- **Best for:** Production / always-on without a server

### Option C — Lightweight VPS / Railway / Render
```
Same as Option A but deployed on a cheap always-on container ($5/mo)
```
- **Best for:** If you want simplicity of Option A but without a local machine dependency

**Recommended starting point:** Option A locally → migrate to B or C once stable.

---

## v1 — MVP

**Goal:** Parse a single reminder format and fire it.

### Stack
- `python-telegram-bot` (v20+, async) — bot framework
- `APScheduler` — in-process job scheduler
- `SQLite` via `sqlite3` — persist reminders across restarts
- `dateparser` — convert "9:30 am", "in 20 minutes", "tomorrow at noon" to a datetime

### Core flow
1. User sends: `remind me at 9:30 am to take out the trash`
2. Bot extracts time + task using `dateparser` + regex
3. Stores `(chat_id, datetime, task)` in SQLite
4. APScheduler schedules a one-shot job for that datetime
5. At fire time, bot sends: `⏰ Reminder: Take out the trash`
6. Bot replies immediately to confirm: `✅ Reminder set for 9:30 AM — "Take out the trash"`

### Files
```
remindmebot/
├── bot.py           # Entry point, polling loop, message handler
├── parser.py        # Natural language → (datetime, task_text)
├── scheduler.py     # APScheduler setup, add/cancel jobs, restore on boot
├── db.py            # SQLite CRUD for reminders
├── .env             # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE
└── requirements.txt
```

---

## v2 — Better UX

- `/list` command → shows all pending reminders with IDs
- `/cancel <id>` command → cancels a reminder
- Snooze button → inline keyboard "Snooze 10 min" on the reminder message
- Smarter parsing: "every day at 8am", "in 2 hours", "next Monday at 3pm"
- Confirmation message includes a Cancel button inline

---

## v3 — Recurring Reminders

- "remind me every Monday at 9am to review my tasks"
- Cron-style recurrence stored in DB
- `/list` shows next fire time + recurrence pattern
- `/cancel` stops the recurrence

---

## v4 — Smarter NLP (Optional)

If regex + dateparser isn't catching edge cases:
- Send the raw message to Claude API with a prompt like:
  > "Extract the reminder time and task from this message. Return JSON: {datetime_iso, task, recurrence}"
- Falls back to asking the user to clarify if extraction fails
- Could also handle: "remind me when I get home" (geofence — stretch goal)

---

## Natural Language Parsing Strategy

Use a layered approach (try each in order):

1. **Regex patterns** for common formats:
   - `remind me (at|in|on) <time> to <task>`
   - `<task> at <time>`
   - `at <time> <task>`

2. **`dateparser.parse()`** on the extracted time string — handles:
   - "9:30 am", "in 20 minutes", "tomorrow at noon", "next Friday at 6pm"
   - Pass `PREFER_DATES_FROM: 'future'` and `TIMEZONE` settings

3. **Claude API fallback** for anything ambiguous (v4)

---

## Key Edge Cases to Handle

| Input | Expected behavior |
|---|---|
| "remind me at 9:30" (no AM/PM) | Assume next occurrence (AM if before noon, PM if after) |
| "remind me in 5 minutes" | Relative time from now |
| "remind me tomorrow at 8" | Next day at 8 AM |
| No time detected | Bot asks: "When should I remind you?" |
| Past time (e.g., 9am when it's 10am) | Bot asks: "That time has passed — did you mean tomorrow?" |
| Multi-message conversation | State machine: ask for time, then task separately |

---

## Timezone Handling
- Store a `TIMEZONE` env var (e.g., `America/New_York`)
- All user-facing times shown in that timezone
- Store reminders in UTC internally
- Future: `/timezone` command to set per-user timezone

---

## Data Model (SQLite)

```sql
CREATE TABLE reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    task        TEXT NOT NULL,
    fire_at     TEXT NOT NULL,  -- ISO 8601 UTC
    recurrence  TEXT,           -- NULL or cron string
    created_at  TEXT NOT NULL,
    fired       INTEGER DEFAULT 0
);
```

---

## Quick Start Plan (What to Build First)

1. [ ] Set up `.env` with `TELEGRAM_TOKEN` (from your QUICKSTART.md)
2. [ ] `pip install python-telegram-bot apscheduler dateparser python-dotenv`
3. [ ] Build `db.py` — create table, insert/list/delete reminders
4. [ ] Build `parser.py` — regex + dateparser, return `(datetime, task)` or `None`
5. [ ] Build `scheduler.py` — wrap APScheduler, restore pending reminders on boot
6. [ ] Build `bot.py` — polling loop, route messages to parser, confirm/fire
7. [ ] Test end-to-end locally
8. [ ] Deploy to VPS or Azure Function

---

## Stretch Goals / Future Ideas

- **Morning briefing**: "Here are your reminders for today"
- **Smart suggestions**: detects patterns ("you remind yourself to call mom every Sunday — want to make that recurring?")
- **Voice note transcription**: user sends a voice message, bot transcribes + parses
- **Group chat reminders**: tag a user in the reminder
- **Calendar sync**: create a Google Calendar event alongside the reminder
- **Buddy features**: check-ins ("Did you complete this?"), streak tracking
