# Telegram Bot Quick Start Guide

## 1. Create a Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name and username (must end in `bot`, e.g. `mytradingbot`)
4. Copy the **token** — you'll need it for every API call

---

## 2. Get Your Chat ID

1. Send any message to your bot
2. Call the API:
   ```
   GET https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Find `result[0].message.chat.id` in the response — that's your chat ID

---

## 3. Send Your First Message

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": "<CHAT_ID>", "text": "Hello World!"}'
```

---

## 4. Core API Methods

| Method | Description |
|---|---|
| `sendMessage` | Send a text message |
| `sendPhoto` | Send an image |
| `sendDocument` | Send a file |
| `getUpdates` | Poll for incoming messages |
| `setWebhook` | Register a webhook URL |
| `deleteWebhook` | Remove the webhook |
| `getMe` | Verify bot token / get bot info |

All methods: `POST https://api.telegram.org/bot<TOKEN>/<method>`

---

## 5. Receiving Messages: Polling vs Webhook

### Polling (simple, good for local dev)
```python
import requests, time

TOKEN = "your-token"

def get_updates(offset=None):
    params = {"timeout": 30, "offset": offset}
    return requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", params=params).json()

offset = None
while True:
    updates = get_updates(offset)
    for update in updates.get("result", []):
        offset = update["update_id"] + 1
        print(update["message"]["text"])
```

### Webhook (production)
Register your Azure Function URL as the webhook:
```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<app>.azurewebsites.net/api/webhook"
```
Telegram will POST every incoming message to that URL.

---

## 6. Message Formatting

Pass `"parse_mode"` in `sendMessage`:

| Mode | Syntax |
|---|---|
| `Markdown` | `*bold*`, `_italic_`, `` `code` ``, ` ```block``` ` |
| `HTML` | `<b>bold</b>`, `<i>italic</i>`, `<code>code</code>` |

```python
payload = {
    "chat_id": chat_id,
    "text": "*Alert!* Price hit `50000`",
    "parse_mode": "Markdown"
}
```

---

## 7. Keyboards & Buttons

### Inline keyboard (buttons inside message)
```python
payload = {
    "chat_id": chat_id,
    "text": "Choose an option:",
    "reply_markup": {
        "inline_keyboard": [[
            {"text": "Buy", "callback_data": "buy"},
            {"text": "Sell", "callback_data": "sell"}
        ]]
    }
}
```

Handle the button press via `callback_query` in your webhook payload.

---

## 8. Common Webhook Payload Structure

When Telegram sends a message to your webhook:

```json
{
  "update_id": 123456,
  "message": {
    "message_id": 1,
    "from": { "id": 6842052912, "first_name": "Jerome" },
    "chat": { "id": 6842052912, "type": "private" },
    "text": "/start"
  }
}
```

For button presses:
```json
{
  "update_id": 123457,
  "callback_query": {
    "id": "abc",
    "from": { "id": 6842052912 },
    "data": "buy"
  }
}
```

---

## 9. Azure Function Webhook Handler Pattern

```python
@app.route(route="webhook", auth_level=func.AuthLevel.ANONYMOUS)
def webhook(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()

    # Regular message
    if "message" in body:
        text = body["message"].get("text", "")
        chat_id = body["message"]["chat"]["id"]
        # handle text...

    # Button press
    elif "callback_query" in body:
        data = body["callback_query"]["data"]
        chat_id = body["callback_query"]["from"]["id"]
        # handle callback...

    return func.HttpResponse("OK", status_code=200)
```

> Always return `200 OK` immediately — if Telegram doesn't get a 200, it will retry the same update repeatedly.

---

## 10. Portfolio‑news script

To send yourself a summary of recent news for every ticker in your holdings, there's
an example helper script in the repository:

```python
# portfolio_bot.py (see file in workspace)
```

It reads the CSV in the workspace and uses `yfinance` to fetch articles for each
symbol. The only two environment variables it needs are the same ones used earlier:

```bash
export TELEGRAM_TOKEN="<your token>"
export TELEGRAM_CHAT_ID="<your chat id>"
python portfolio_bot.py
```

Install dependencies with `pip install -r requirements.txt` first.  If you prefer
not to use `yfinance`, you can replace the `fetch_news_for_symbol` function with
any other API call (e.g. NewsAPI, FinancialModelingPrep, etc.).

### Scheduling the script

- **Linux/macOS/WSL**: add a cron entry to run the script each morning, e.g.
  `0 8 * * * cd /path/to/repo && /usr/bin/python3 portfolio_bot.py`.

- **Windows**: use Task Scheduler to create a basic task that runs the same
  command daily at a time of your choosing.

- **Azure Functions**: convert `portfolio_bot.py` into a timer-triggered function
  (`@app.schedule`) and deploy; the `main` function can remain unchanged.

> The message body is formatted with Markdown, so long updates may be clipped by
> Telegram; consider limiting the number of symbols or sending multiple messages.

---

## 10. Local Settings Reference

```json
{
  "Values": {
    "TELEGRAM_TOKEN": "your-bot-token",
    "TELEGRAM_CHAT_ID": "your-chat-id"
  }
}
```

Never commit `local.settings.json` — it's in `.gitignore`. Set these as **Application Settings** in Azure Portal for production.

---

## Useful Links

- Telegram Bot API docs: https://core.telegram.org/bots/api
- BotFather: https://t.me/BotFather
- Azure Functions Python dev guide: https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python
