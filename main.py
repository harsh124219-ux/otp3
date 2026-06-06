"""
main.py — OTP Ocean Bot entry point.
Registers all handlers, enforces routing priority, starts keep-alive web server.

Routing priority for private messages (critical — never change order):
  1. payment_admin_states  (rejection reason from admin)
  2. session_states        (live login flow)
  3. admin_states          (addacc / upi image / fsub prompt)
  4. user_states           (deposit flow)
  5. fallback              (silently ignored)
"""

import asyncio
import logging
import os
import sys
import traceback

import aiohttp
import aiohttp.web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

# ─────────────────────────────────────────────
#  LOGGING — must be first
# ─────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream  = sys.stdout,
    force   = True,
)
logger = logging.getLogger("main")


# ─────────────────────────────────────────────
#  IMPORTS (with startup diagnostics)
# ─────────────────────────────────────────────

def _import_or_die(module_path: str, label: str):
    import importlib
    try:
        mod = importlib.import_module(module_path)
        logger.info(f"✅ Imported: {label}")
        return mod
    except Exception as e:
        logger.critical(f"❌ IMPORT FAILED [{label}]: {e}")
        traceback.print_exc()
        sys.exit(1)


info_mod    = _import_or_die("info",             "info")
db_mod      = _import_or_die("database",         "database")
user_mod    = _import_or_die("handlers.user",    "handlers.user")
shop_mod    = _import_or_die("handlers.shop",    "handlers.shop")
admin_mod   = _import_or_die("handlers.admin",   "handlers.admin")
payment_mod = _import_or_die("handlers.payment", "handlers.payment")
session_mod = _import_or_die("handlers.session", "handlers.session")
fsub_mod    = _import_or_die("handlers.fsub",    "handlers.fsub")

# Unpack everything we need
from info import BOT_TOKEN, API_ID, API_HASH, ADMIN_ID, PORT

from database import init_db, is_admin

from handlers.user import (
    start, balance_cmd, profile_menu, deposit_menu,
    orders_menu, help_menu, help_detail, handle_message,
    user_states,
)
from handlers.shop import (
    shop_menu, sort_options_menu, view_country_accounts,
    buy_account, get_otp_logic, logout_acc_logic,
)
from handlers.admin import (
    stats, add_bal, broadcast, manage_admins, set_config_cmd,
    show_fsub_manager, show_rm_fsub_menu, sold_accounts,
    add_acc_start, set_upi_image_start, handle_admin_msg,
    admin_states,
)
from handlers.payment import (
    payment_callback, handle_admin_rejection_reason,
    payment_admin_states,
)
from handlers.session import (
    login_command, cancel_login_command, handle_session_message,
    handle_automation_callback, check_incomplete_sessions,
    session_states,
)
from handlers.fsub import check_fsub, recheck_fsub_callback
from keep_alive import start_keep_alive


# ─────────────────────────────────────────────
#  BOT CLIENT
# ─────────────────────────────────────────────

app = Client(
    name                        = "otp_ocean_main",
    api_id                      = API_ID,
    api_hash                    = API_HASH,
    bot_token                   = BOT_TOKEN.strip(),
    max_concurrent_transmissions = 3,
)


# ─────────────────────────────────────────────
#  FSub-guarded callbacks (check before routing)
# ─────────────────────────────────────────────
_FSUB_GUARDED = {"open_shop", "open_deposit", "open_orders", "open_profile"}

# All known user + admin commands (for the ~filters.command exclusion)
_USER_CMDS  = ["start", "help", "shop", "orders", "balance", "profile"]
_ADMIN_CMDS = [
    "stats", "addbal", "broadcast", "addadmin", "rmadmin",
    "setfsub", "setupi", "addacc", "recovery", "fa2", "sold",
    "login", "cancellogin",
]
_ALL_CMDS = _USER_CMDS + _ADMIN_CMDS


# ─────────────────────────────────────────────
#  DEBUG: Raw update logger
# ─────────────────────────────────────────────

@app.on_raw_update()
async def raw_update_handler(client, update, users, chats):
    logger.debug(f"RAW: {type(update).__name__}")


# ─────────────────────────────────────────────
#  DEBUG: Global message logger (group=-1)
# ─────────────────────────────────────────────

@app.on_message(filters.private, group=-1)
async def global_debug_logger(client, message: Message):
    uid  = message.from_user.id if message.from_user else "?"
    text = (message.text or "[non-text]")[:60]
    logger.info(f"📨 MSG from {uid}: {text}")


# ─────────────────────────────────────────────
#  USER COMMANDS
# ─────────────────────────────────────────────

@app.on_message(
    filters.command(_USER_CMDS) & filters.private
)
async def user_commands_handler(client, message: Message):
    cmd = message.command[0].lower()
    if cmd == "start":
        await start(client, message)
    elif cmd == "help":
        await help_menu(client, message)
    elif cmd == "shop":
        await shop_menu(client, message)
    elif cmd == "orders":
        await orders_menu(client, message)
    elif cmd == "balance":
        await balance_cmd(client, message)
    elif cmd == "profile":
        await profile_menu(client, message)


# ─────────────────────────────────────────────
#  ADMIN COMMANDS
# ─────────────────────────────────────────────

@app.on_message(
    filters.command(_ADMIN_CMDS) & filters.private
)
async def admin_commands_handler(client, message: Message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ **Access Denied.** This command is for admins only.")
        return

    cmd = message.command[0].lower()

    if cmd == "stats":
        await stats(client, message)
    elif cmd == "addbal":
        await add_bal(client, message)
    elif cmd == "broadcast":
        await broadcast(client, message)
    elif cmd in ("addadmin", "rmadmin"):
        await manage_admins(client, message)
    elif cmd in ("setupi", "setfsub", "recovery", "fa2"):
        await set_config_cmd(client, message)
    elif cmd == "addacc":
        await add_acc_start(client, message)
    elif cmd == "sold":
        await sold_accounts(client, message)
    elif cmd == "login":
        await login_command(client, message)
    elif cmd == "cancellogin":
        await cancel_login_command(client, message)


# ─────────────────────────────────────────────
#  GENERIC MESSAGE HANDLER (deposit + admin states)
#  Priority: payment_admin_states → session_states
#           → admin_states → user_states → ignore
# ─────────────────────────────────────────────

@app.on_message(
    filters.private & ~filters.command(_ALL_CMDS)
)
async def generic_message_handler(client, message: Message):
    if not message.from_user:
        return

    user_id = message.from_user.id

    # 1. Admin rejection reason (payment flow)
    if user_id in payment_admin_states:
        await handle_admin_rejection_reason(client, message)
        return

    # 2. Live login session (session flow)
    if user_id in session_states:
        await handle_session_message(client, message)
        return

    # 3. Admin interactive flow (addacc / upi image / fsub)
    if is_admin(user_id) and user_id in admin_states:
        await handle_admin_msg(client, message)
        return

    # 4. User deposit flow
    await handle_message(client, message)


# ─────────────────────────────────────────────
#  CALLBACK QUERY HANDLER
# ─────────────────────────────────────────────

@app.on_callback_query()
async def callback_handler(client, callback: CallbackQuery):
    data    = callback.data or ""
    user_id = callback.from_user.id

    # Auto-answer all callbacks except payment ones (they have custom handling)
    is_payment_cb = data.startswith("approve_") or data.startswith("reject_")
    if not is_payment_cb:
        try:
            await callback.answer()
        except Exception:
            pass

    # ── FSub guard for protected actions ────────
    if data in _FSUB_GUARDED:
        if not await check_fsub(client, callback):
            return

    # ── Route by callback data ───────────────────

    # Navigation
    if data == "back_to_main":
        await start(client, callback)

    elif data == "cancel_deposit":
        user_states.pop(user_id, None)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await start(client, callback)

    elif data == "check_fsub_again":
        await recheck_fsub_callback(client, callback)

    # Main menu sections
    elif data == "open_shop":
        await shop_menu(client, callback)

    elif data == "open_deposit":
        await deposit_menu(client, callback)

    elif data == "open_profile":
        await profile_menu(client, callback)

    elif data == "open_orders":
        await orders_menu(client, callback)

    elif data == "open_help":
        await help_menu(client, callback)

    elif data == "open_rules":
        from info import RULES_TEXT
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
        ])
        try:
            await callback.message.delete()
        except Exception:
            pass
        await client.send_message(user_id, RULES_TEXT, reply_markup=markup)

    elif data == "open_support":
        from info import SUPPORT_TEXT
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
        ])
        try:
            await callback.message.delete()
        except Exception:
            pass
        await client.send_message(user_id, SUPPORT_TEXT, reply_markup=markup)

    elif data == "open_balance":
        from database import get_balance
        bal    = get_balance(user_id)
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 Deposit", callback_data="open_deposit")],
            [InlineKeyboardButton("🔙 Back",    callback_data="back_to_main")],
        ])
        try:
            await callback.message.delete()
        except Exception:
            pass
        await client.send_message(
            user_id,
            f"💰 **Your Wallet Balance**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**₹{bal:.2f}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Tap below to add more funds!",
            reply_markup=markup
        )

    # Help details
    elif data in ("help_user", "help_admin"):
        await help_detail(client, callback)

    # Shop flow
    elif data.startswith("sort_opts_"):
        await sort_options_menu(client, callback)

    elif data.startswith("view_country_"):
        await view_country_accounts(client, callback)

    elif data.startswith("buy_acc_"):
        await buy_account(client, callback)

    # OTP fetch
    elif data.startswith("get_otp_"):
        await get_otp_logic(client, callback)

    # Logout (both steps: confirm warning + actual logout)
    elif data.startswith("logout_confirm_") or data.startswith("logout_acc_"):
        await logout_acc_logic(client, callback)

    # Payment approval/rejection (admin only, in LOG_GROUP)
    elif is_payment_cb:
        await payment_callback(client, callback)

    # Automation choice (session flow)
    elif data in ("setup_all", "setup_clean", "setup_sec", "setup_skip"):
        await handle_automation_callback(client, callback)

    # Admin: UPI image
    elif data == "set_upi_image":
        await set_upi_image_start(client, callback)

    # Admin: FSub management
    elif data == "setfsub_menu" or data == "open_setfsub":
        if is_admin(user_id):
            await show_fsub_manager(client, callback)
        else:
            await callback.answer("❌ Admin only!", show_alert=True)

    elif data == "add_fsub_prompt":
        if not is_admin(user_id):
            await callback.answer("❌ Admin only!", show_alert=True)
            return
        admin_states[user_id] = {"step": "waiting_fsub_channel"}
        try:
            await callback.message.delete()
        except Exception:
            pass
        await client.send_message(
            user_id,
            "📢 **Add FSub Channel**\n\n"
            "Enter the channel ID or @username:\n\n"
            "Examples:\n"
            "• `@MyChannel`\n"
            "• `-1001234567890`"
        )

    elif data == "rm_fsub_menu":
        if not is_admin(user_id):
            await callback.answer("❌ Admin only!", show_alert=True)
            return
        await show_rm_fsub_menu(client, callback)

    elif data.startswith("rm_fsub_"):
        if not is_admin(user_id):
            await callback.answer("❌ Admin only!", show_alert=True)
            return
        from database import remove_fsub_channel
        channel = data.replace("rm_fsub_", "")
        remove_fsub_channel(channel)
        await callback.answer(f"✅ Removed: {channel}", show_alert=False)
        await show_fsub_manager(client, callback)

    else:
        logger.warning(f"⚠️ Unhandled callback: {data!r} from {user_id}")
        await callback.answer("⚠️ Unknown action.", show_alert=True)


# ─────────────────────────────────────────────
#  KEEP-ALIVE WEB SERVER (Heroku requirement)
# ─────────────────────────────────────────────

async def start_web_server():
    web_app = aiohttp.web.Application()
    web_app.router.add_get("/", lambda r: aiohttp.web.Response(text="🌊 OTP Ocean is alive!"))
    web_app.router.add_get("/health", lambda r: aiohttp.web.Response(text="OK"))

    runner = aiohttp.web.AppRunner(web_app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌐 Web server running on port {PORT}")
    return runner


# ─────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────

async def main():
    logger.info("🌊 ─── OTP Ocean Bot Starting ───")

    # 1. Validate critical env vars
    missing = []
    if not BOT_TOKEN:   missing.append("BOT_TOKEN")
    if not API_ID:      missing.append("API_ID")
    if not API_HASH:    missing.append("API_HASH")
    if not ADMIN_ID:    missing.append("ADMIN_ID")
    if not LOG_GROUP:   missing.append("LOG_GROUP")
    if missing:
        logger.critical(f"❌ Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    # 2. Connect to MongoDB
    logger.info("🍃 Connecting to MongoDB...")
    try:
        init_db()
    except Exception as e:
        logger.critical(f"❌ MongoDB connection failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 3. Start bot and web server
    web_runner = None
    async with app:
        me = await app.get_me()
        logger.info(f"🤖 Bot started: @{me.username} (ID: {me.id})")

        # 4. Start keep-alive web server
        try:
            web_runner = await start_web_server()
        except Exception as e:
            logger.warning(f"⚠️ Web server failed to start (non-fatal): {e}")

        # 5. Notify primary admin that bot is online
        try:
            await app.send_message(
                ADMIN_ID,
                f"🌊 **OTP Ocean is Online!**\n\n"
                f"🤖 Bot: @{me.username}\n"
                f"📅 Started at: {_now_ist()}\n\n"
                f"All systems operational ✅"
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not send startup message to admin: {e}")

        # 6. Check for incomplete login sessions (restart recovery)
        asyncio.create_task(check_incomplete_sessions(app))

        # 7. Start keep-alive self-ping (prevents Render/Railway sleep)
        start_keep_alive()

        logger.info("🟢 Bot is ready and polling!")

        # 7. Run until stopped
        await idle()

    # 8. Cleanup
    if web_runner:
        await web_runner.cleanup()
    logger.info("🔴 Bot stopped.")


def _now_ist() -> str:
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by KeyboardInterrupt.")
    except Exception as e:
        logger.critical(f"💥 Critical error in main: {e}")
        traceback.print_exc()
        sys.exit(1)
