# 🌊 OTP Ocean — Deployment Guide
## Render & Railway (Step-by-Step)

---

## 📋 BEFORE YOU START — Prerequisites

You need these ready before deploying anywhere:

| Item | Where to get it |
|------|----------------|
| `BOT_TOKEN` | Message @BotFather on Telegram → `/newbot` |
| `API_ID` + `API_HASH` | https://my.telegram.org → App configuration |
| `ADMIN_ID` | Message @userinfobot on Telegram |
| `LOG_GROUP` | Create a Telegram group → add your bot as admin → get its ID from @getidsbot |
| `MONGO_URL` | https://cloud.mongodb.com (free M0 cluster) |

### How to get your MongoDB URL (free):
1. Go to https://cloud.mongodb.com
2. Click **Create** → choose **M0 Free**
3. Pick any region → Create Cluster
4. Go to **Database Access** → Add User → set username/password
5. Go to **Network Access** → Add IP → choose **Allow from anywhere** (`0.0.0.0/0`)
6. Go to your cluster → **Connect** → **Drivers** → copy the connection string
7. Replace `<password>` in the string with your actual password

### How to get LOG_GROUP ID:
1. Create a new Telegram group
2. Add your bot to the group as **admin**
3. Add @getidsbot to the group
4. It will show the group ID (negative number like `-1001234567890`)
5. Remove @getidsbot, keep your bot as admin

---

## 🚀 OPTION A — Deploy on Render (Recommended for beginners)

Render has a **free tier** that works well. The keep_alive.py pinger prevents sleep.

### Step 1 — Push code to GitHub

```bash
# On your computer (install git if you don't have it)
cd otp_ocean

git init
git add .
git commit -m "🌊 Initial OTP Ocean deployment"

# Go to https://github.com/new → create a NEW PRIVATE repo
# Then run:
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git branch -M main
git push -u origin main
```

> ⚠️ **IMPORTANT:** Make sure `.env` is in your `.gitignore` (it already is).
> **NEVER push your `.env` file to GitHub.**

---

### Step 2 — Create a Render account

1. Go to https://render.com
2. Sign up with GitHub (recommended — makes deployment easier)
3. Authorize Render to access your GitHub

---

### Step 3 — Create a new Web Service on Render

1. Click **New +** → **Web Service**
2. Connect your GitHub repo (`YOUR_REPO_NAME`)
3. Fill in the settings:

| Setting | Value |
|---------|-------|
| **Name** | `otp-ocean-bot` |
| **Region** | Singapore *(closest to India)* |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Plan** | Free |

4. Click **Advanced** → scroll to **Environment Variables**

---

### Step 4 — Set environment variables on Render

Click **Add Environment Variable** for each one:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Your bot token from BotFather |
| `API_ID` | Your API ID (number only) |
| `API_HASH` | Your API hash |
| `ADMIN_ID` | Your Telegram user ID |
| `LOG_GROUP` | Your log group ID (negative number) |
| `MONGO_URL` | Your MongoDB Atlas connection string |
| `PORT` | `8080` |
| `APP_URL` | Leave **blank for now** — fill after first deploy |

5. Click **Create Web Service**

---

### Step 5 — Wait for first deploy

- Render will install dependencies and start the bot
- Watch the **Logs** tab — you should see:
  ```
  ✅ MongoDB connected successfully.
  🤖 Bot started: @YourBotName
  🌐 Web server running on port 8080
  🟢 Bot is ready and polling!
  ```
- If you see errors, check Step 6 (troubleshooting)

---

### Step 6 — Set APP_URL to prevent sleep

After the first successful deploy:

1. Copy your Render URL from the top of the page
   - It looks like: `https://otp-ocean-bot.onrender.com`
2. Go to **Environment** tab → find `APP_URL`
3. Set it to your URL: `https://otp-ocean-bot.onrender.com`
4. Click **Save Changes** → Render will redeploy automatically
5. Now `keep_alive.py` will ping `/health` every 14 minutes — **no more sleep!**

---

### Step 7 — Verify the bot is working

1. Open Telegram → message your bot `/start`
2. You should see the welcome menu
3. Check the Render logs — should show `📨 MSG from YOUR_ID: /start`

---

### Render — Auto-deploy on code updates

Every time you `git push` to the `main` branch, Render will automatically
redeploy. No manual steps needed.

```bash
# Make a change, then:
git add .
git commit -m "fix: updated something"
git push origin main
# Render auto-deploys in ~2 minutes
```

---

## 🚂 OPTION B — Deploy on Railway

Railway has a **$5 free credit/month** which is enough for a Telegram bot.
No sleep issues on the hobby plan.

### Step 1 — Push code to GitHub

Same as Render Step 1 above. Same repo works for both.

---

### Step 2 — Create a Railway account

1. Go to https://railway.app
2. Sign up with GitHub
3. Verify your account (Railway requires phone verification for free tier)

---

### Step 3 — Create a new project on Railway

1. Click **New Project**
2. Select **Deploy from GitHub repo**
3. Authorize Railway → select your repo
4. Railway will auto-detect Python and start building

---

### Step 4 — Set environment variables on Railway

1. Click on your service (the box that appears)
2. Go to the **Variables** tab
3. Click **New Variable** for each:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Your bot token |
| `API_ID` | Your API ID |
| `API_HASH` | Your API hash |
| `ADMIN_ID` | Your Telegram user ID |
| `LOG_GROUP` | Your log group ID |
| `MONGO_URL` | Your MongoDB connection string |
| `PORT` | `8080` |
| `APP_URL` | Leave blank for now |

4. Railway will automatically redeploy when you save variables

---

### Step 5 — Generate a public domain

1. Go to **Settings** tab of your service
2. Scroll to **Networking** → **Public Networking**
3. Click **Generate Domain**
4. Copy the URL (like `otp-ocean.up.railway.app`)

---

### Step 6 — Set APP_URL on Railway

1. Go back to **Variables** tab
2. Set `APP_URL` = `https://otp-ocean.up.railway.app`
3. Railway redeploys automatically
4. Keep-alive pinger activates — bot stays warm!

---

### Step 7 — Check logs

1. Go to the **Deployments** tab
2. Click the latest deployment
3. Click **View Logs**
4. Look for:
   ```
   ✅ MongoDB connected successfully.
   🤖 Bot started: @YourBotName
   🟢 Bot is ready and polling!
   ```

---

## ⚙️ FIRST-TIME BOT SETUP (after deploying anywhere)

Once the bot is running, do this setup via Telegram:

### 1. Set up UPI payment details
```
/setupi yourname@upi Your Name
```
Example: `/setupi rahul@paytm Rahul Kumar`

### 2. Upload QR code image (optional but recommended)
After running `/setupi`, tap the **🖼️ Upload QR Code Image** button
and send a photo of your UPI QR code.

### 3. Set default 2FA password for new accounts
```
/fa2 MyStrongPassword123
```

### 4. Set default recovery email for new accounts
```
/recovery youremail@gmail.com
```

### 5. Set force-subscribe channel (optional)
```
/setfsub
```
Then tap ➕ Add Channel and enter `@YourChannelUsername`

### 6. Add your first account to the shop
**Option A — Via /login (recommended):**
```
/login
```
Follow the interactive prompts to log in and auto-setup an account.

**Option B — Manual (if you already have a session string):**
```
/addacc
```
Follow the prompts to enter phone, session string, country, price.

### 7. Verify everything works
- Send `/stats` — should show DB connected and counts
- Send `/start` — main menu should appear
- Go to Shop — your account should be listed

---

## 🔧 TROUBLESHOOTING

### ❌ "MongoDB connection failed"
- Check your `MONGO_URL` — ensure password has no special characters
  (if it does, URL-encode them: `@` → `%40`, `#` → `%23`)
- Check MongoDB Atlas → Network Access → ensure `0.0.0.0/0` is whitelisted

### ❌ "Import failed: handlers.user" or similar
- Your repo structure must match exactly:
  ```
  main.py
  info.py
  database.py
  keep_alive.py
  requirements.txt
  handlers/
    __init__.py
    user.py
    shop.py
    admin.py
    payment.py
    session.py
    fsub.py
  ```

### ❌ Bot starts but doesn't respond
- Check `BOT_TOKEN` — must be the full token from BotFather
- Ensure no extra spaces in env vars

### ❌ "Flood wait" during /login
- Wait the specified seconds, then try again
- This is Telegram's rate limit — normal behavior

### ❌ Render goes to sleep despite keep_alive
- Ensure `APP_URL` is set correctly (no trailing slash)
- The free tier may still sleep occasionally — upgrade to Starter ($7/mo) for zero sleep

### ❌ Railway "out of credits"
- Railway gives $5/month free. A bot uses ~$1-2/month
- Add a payment method to get $5/month instead of being blocked

---

## 🔄 UPDATING THE BOT

```bash
# Make your code changes, then:
git add .
git commit -m "update: description of changes"
git push origin main

# Both Render and Railway auto-deploy on push
# Bot will restart with new code in ~2-3 minutes
```

---

## 📊 MONITORING

### Render
- **Logs**: Dashboard → Your Service → Logs tab
- **Metrics**: Dashboard → Your Service → Metrics tab
- **Alerts**: Set up email alerts in Account Settings

### Railway
- **Logs**: Dashboard → Your Service → Deployments → View Logs
- **Metrics**: Dashboard → Your Service → Metrics

---

## 💡 TIPS

1. **Use MongoDB Atlas free tier** — it's reliable and has 512MB storage
   (enough for thousands of users and orders)

2. **Keep your GitHub repo private** — your code has your bot structure;
   even without secrets it's good practice

3. **Set up a UptimeRobot monitor** (free) at https://uptimerobot.com
   as a backup pinger:
   - Add HTTP(S) monitor pointing to `https://your-app-url.com/health`
   - Set check interval to 5 minutes
   - This gives you double protection against sleep

4. **Back up your .env values** in a password manager —
   if you lose them you'll need to regenerate everything

5. **Railway is more reliable** than Render free tier for bots —
   Render free has sleep, Railway Hobby ($5/mo) has no sleep at all
