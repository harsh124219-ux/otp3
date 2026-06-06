"""
info.py — All environment variables, configuration constants, and text strings.
Edit text here without ever touching handler logic.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
#  CORE ENVIRONMENT VARIABLES
# ─────────────────────────────────────────────

BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
API_ID      = int(os.environ.get("API_ID", 0))
API_HASH    = os.environ.get("API_HASH", "")
ADMIN_ID    = int(os.environ.get("ADMIN_ID", 0))
LOG_GROUP   = int(os.environ.get("LOG_GROUP", 0))
MONGO_URL   = os.environ.get("MONGO_URL", "")
PORT        = int(os.environ.get("PORT", 8080))
HEROKU_APP  = os.environ.get("HEROKU_APP_NAME", "")

# ─────────────────────────────────────────────
#  USER-FACING TEXT STRINGS
# ─────────────────────────────────────────────

START_MESSAGE = """
🌊 **Welcome to OTP Ocean, {name}!**

Your one-stop shop for pre-loaded Telegram accounts.
Get OTP codes instantly for seamless Telegram logins.

━━━━━━━━━━━━━━━━━━━━━━━━
🛒 **Shop** — Browse accounts by country
💵 **Deposit** — Top up your wallet via UPI
📦 **Orders** — View your purchases & fetch OTPs
👤 **Profile** — Your stats & balance
━━━━━━━━━━━━━━━━━━━━━━━━

💡 _Tap any button below to get started!_
"""

RULES_TEXT = """
📋 **OTP Ocean — Rules & Policy**

━━━━━━━━━━━━━━━━━━━━━━━━
🔐 **Account Security**
• Change the 2FA password immediately after purchase.
• Do NOT share your session or credentials with anyone.
• We are not responsible for accounts lost due to negligence.

🚫 **Prohibited Activities**
• Using accounts for spam, scam, or illegal activities.
• Reselling purchased accounts to third parties.
• Attempting to abuse the OTP fetch system.

💸 **Refund Policy**
• Refunds are only issued if an account is non-functional at the time of purchase.
• No refunds after OTP has been successfully fetched.
• All disputes must be raised within 24 hours of purchase.

📞 **Support**
• Contact @OTPOceanSupportBot for all issues.
• Response time: within 2–6 hours.

⚠️ _Violation of these rules may result in a permanent ban._
"""

SUPPORT_TEXT = """
🛟 **OTP Ocean — Support Center**

━━━━━━━━━━━━━━━━━━━━━━━━
📬 **Contact Us:** @OTPOceanSupportBot

🔧 **Common Issues & Fixes:**

❓ _OTP not showing?_
→ Trigger a login attempt first, then tap 🔄 Refresh.

❓ _Payment not approved?_
→ Ensure your UTR is correct (12–22 digits). Allow up to 30 mins.

❓ _Account not working?_
→ Contact support within 24 hours with your Order ID.

❓ _Balance deducted but no account received?_
→ Check My Orders — your purchase may still be there.

━━━━━━━━━━━━━━━━━━━━━━━━
⏰ _Support hours: 9 AM – 11 PM IST_
"""

PROFILE_TEXT = """
👤 **Your Profile**

━━━━━━━━━━━━━━━━━━━━━━━━
🏷️ **Name:** {name}
🆔 **User ID:** `{user_id}`
💰 **Wallet Balance:** ₹{balance}
💸 **Total Spent:** ₹{total_spent}
📦 **Total Purchases:** {total_purchases}
━━━━━━━━━━━━━━━━━━━━━━━━
"""

HELP_TEXT = """
📖 **OTP Ocean — Help Menu**

Choose a category below to see available commands:
"""

USER_HELP = """
👤 **User Commands**

━━━━━━━━━━━━━━━━━━━━━━━━
/start — 🏠 Open the main menu
/shop — 🛒 Browse available accounts
/orders — 📦 View your orders & fetch OTPs
/balance — 💰 Check your wallet balance
/profile — 👤 View your profile & stats
/help — 📖 Show this help menu
━━━━━━━━━━━━━━━━━━━━━━━━
"""

ADMIN_HELP = """
🔐 **Admin Commands**

━━━━━━━━━━━━━━━━━━━━━━━━
/stats — 📊 View bot statistics
/addbal `<user_id>` `<amount>` — ➕ Add balance to user
/broadcast `<text>` — 📢 Send message to all users
/addadmin `<user_id>` — 👑 Promote user to admin
/rmadmin `<user_id>` — 🚫 Remove admin (can't remove primary)
/setupi `<upi_id>` `<name>` — 💳 Set UPI payment details
/setfsub — 📢 Manage force-subscribe channels
/addacc — ➕ Add a new account (interactive)
/login — 🔑 Login & auto-setup a new account
/cancellogin — ❌ Force-cancel a stuck login session
/recovery `<email>` — 📧 Set default recovery email
/fa2 `<password>` — 🔐 Set default 2FA password
/sold — 📋 View recent sold accounts
━━━━━━━━━━━━━━━━━━━━━━━━
"""

DEPOSIT_TEXT = """
💵 **Deposit Funds**

━━━━━━━━━━━━━━━━━━━━━━━━
💳 **UPI ID:** `{upi_id}`
👤 **Name:** {upi_name}
━━━━━━━━━━━━━━━━━━━━━━━━

📋 **Steps to deposit:**
1️⃣ Send money via any UPI app to the ID above
2️⃣ Come back here and enter the **amount** you paid
3️⃣ Send a **screenshot** of your payment
4️⃣ Enter your **UTR / Transaction ID** (12–22 digits)

⏰ _Approvals usually within 5–30 minutes._
"""

NO_UPI_TEXT = """
⚠️ **Payment Not Configured**

The admin has not set up UPI payment details yet.
Please contact support: @OTPOceanSupportBot
"""
