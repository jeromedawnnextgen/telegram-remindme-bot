# Deploy to Railway

## One-time setup

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "initial commit"
# create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/remindmebot.git
git push -u origin main
```

### 2. Create Railway project
1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Select your `remindmebot` repo

### 3. Add a persistent volume (keeps reminders across deploys)
1. In Railway dashboard, click your service → **Add Volume**
2. Set mount path to `/data`
3. That's it — Railway handles the rest

### 4. Set environment variables
In Railway dashboard → your service → **Variables**, add:

| Key | Value |
|---|---|
| `TELEGRAM_TOKEN` | your bot token |
| `ALLOWED_CHAT_ID` | your Telegram chat ID |
| `TIMEZONE` | e.g. `America/New_York` |
| `DATA_DIR` | `/data` |

### 5. Deploy
Railway auto-deploys on every push to `main`. The first deploy triggers automatically after step 2.

Check **Logs** in the Railway dashboard to confirm the bot started:
```
INFO Starting RemindMeBot...
INFO Restored N pending reminders from DB
```

---

## Running locally

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Copy env file and fill in values
cp .env.example .env
# edit .env — ALLOWED_CHAT_ID is already set if you followed QUICKSTART.md

# 3. Run
python bot.py
```

> For local runs, DATA_DIR defaults to `.` so reminders.db is created in the project folder.

---

## Updating the bot

```bash
git add .
git commit -m "your change"
git push
```

Railway redeploys automatically. Reminders in the volume survive the redeploy.
