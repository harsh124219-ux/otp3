"""
handlers/session.py — Interactive admin login flow with full automation:
  • Live login with OTP/2FA
  • Leave groups, block contacts, change 2FA, set recovery email
  • Export session string and save to DB
  • Heroku restart recovery: detects incomplete sessions on startup and notifies admins
"""

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta

from pyrogram import Client as PyroClient
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
)
from pyrogram.enums import ChatType
from pyrogram.errors import (
    FloodWait,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
)

from database import (
    add_account,
    get_config,
    update_config,
    is_admin,
    _col,
)
from info import API_ID, API_HASH, LOG_GROUP

logger = logging.getLogger(__name__)

# ── Session flow state: {admin_id: {"step": str, "temp_client": Client, ...}} ──
session_states: dict = {}

# Steps:
# waiting_phone → waiting_code → [waiting_password] → waiting_country
# → waiting_price → waiting_menu_choice → [waiting_new_2fa] → [waiting_recovery_email]
# → [waiting_email_otp] → running → done


def _now_ist() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


# ─────────────────────────────────────────────
#  CLEANUP
# ─────────────────────────────────────────────

async def _cleanup_state(admin_id: int) -> None:
    """Disconnect temp client and remove state."""
    state = session_states.pop(admin_id, None)
    if state and state.get("temp_client"):
        try:
            await state["temp_client"].disconnect()
        except Exception:
            pass


async def _notify_failure(bot, admin_id: int, phone: str, error: str) -> None:
    """Send a formatted failure notice to admin."""
    try:
        await bot.send_message(
            admin_id,
            f"❌ **Login Failed**\n\n"
            f"📱 Phone: `{phone}`\n"
            f"🔴 Error: `{error[:300]}`\n\n"
            f"Restart the process with /login."
        )
    except Exception:
        pass


# ─────────────────────────────────────────────
#  HEROKU RESTART RECOVERY
# ─────────────────────────────────────────────

async def check_incomplete_sessions(bot) -> None:
    """
    Called on bot startup. Scans for accounts in DB that are marked as
    'processing' (a flag set during /login flow) and notifies all admins
    to re-run /login for those numbers.
    We store a 'processing' status on accounts mid-login. On Heroku restart,
    these would be abandoned — we alert admins.
    """
    await asyncio.sleep(3)  # Wait for bot to fully start

    try:
        # Find any accounts that were stuck mid-processing
        stuck = list(_col("accounts").find({"status": "processing"}))
        if not stuck:
            return

        cfg    = get_config()
        admins = cfg.get("admins", [])

        for acc in stuck:
            # Reset status back to available or remove the stuck record
            _col("accounts").delete_one({"phone": acc["phone"], "status": "processing"})

            phone = acc.get("phone", "unknown")
            msg   = (
                "⚠️ **Incomplete Login Detected!**\n\n"
                "The bot restarted (likely Heroku restart) while a login was in progress.\n\n"
                f"📱 **Phone:** `{phone}`\n"
                f"⏰ **Started:** {acc.get('added_at', 'unknown')}\n\n"
                "The session was **not saved**. Please run /login again for this number."
            )
            for admin_id in admins:
                try:
                    await bot.send_message(admin_id, msg)
                except Exception as e:
                    logger.warning(f"⚠️ Could not notify admin {admin_id}: {e}")

        logger.info(f"✅ Recovery check done. Notified admins about {len(stuck)} stuck session(s).")

    except Exception as e:
        logger.error(f"❌ Recovery check error: {e}")


# ─────────────────────────────────────────────
#  /login COMMAND
# ─────────────────────────────────────────────

async def login_command(client, message: Message) -> None:
    """Start the interactive login flow. /login"""
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        return

    # Clean up any lingering state/client
    await _cleanup_state(admin_id)

    session_states[admin_id] = {"step": "waiting_phone"}

    await message.reply(
        "🔑 **Add Account via Login — Step 1/5**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📱 Enter the phone number with country code:\n\n"
        "Example: `+919876543210`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "_Send /cancellogin at any time to abort._"
    )


# ─────────────────────────────────────────────
#  /cancellogin COMMAND
# ─────────────────────────────────────────────

async def cancel_login_command(client, message: Message) -> None:
    """Force-cancel any in-progress login session for this admin."""
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        return

    if admin_id in session_states:
        state = session_states.get(admin_id, {})
        phone = state.get("phone", "unknown")
        await _cleanup_state(admin_id)
        await message.reply(
            f"❌ **Login Cancelled!**\n\n"
            f"📱 Phone: `{phone}`\n"
            f"All state has been cleared.\n\n"
            f"Run /login to start again."
        )
    else:
        await message.reply("ℹ️ No active login session to cancel.")


# ─────────────────────────────────────────────
#  SESSION MESSAGE HANDLER (state machine)
# ─────────────────────────────────────────────

async def handle_session_message(client, message: Message) -> None:
    """
    Called from main.py when admin_id is in session_states.
    Routes to correct step handler.
    """
    admin_id = message.from_user.id
    state    = session_states.get(admin_id)
    if not state:
        return

    step = state.get("step")

    if step == "waiting_phone":
        await _step_phone(client, message, admin_id, state)

    elif step == "waiting_code":
        await _step_code(client, message, admin_id, state)

    elif step == "waiting_password":
        await _step_password(client, message, admin_id, state)

    elif step == "waiting_country":
        await _step_country(client, message, admin_id, state)

    elif step == "waiting_price":
        await _step_price(client, message, admin_id, state)

    elif step == "waiting_new_2fa":
        await _step_new_2fa(client, message, admin_id, state)

    elif step == "waiting_recovery_email":
        await _step_recovery_email(client, message, admin_id, state)

    elif step == "waiting_email_otp":
        await _step_email_otp(client, message, admin_id, state)

    elif step == "running":
        await message.reply("⏳ Automation is still running. Please wait...")

    elif step == "done":
        await message.reply("✅ This session is complete. Run /login to add another account.")


# ─────────────────────────────────────────────
#  STEP: PHONE NUMBER
# ─────────────────────────────────────────────

async def _step_phone(client, message: Message, admin_id: int, state: dict) -> None:
    phone = (message.text or "").strip()
    if not phone.startswith("+") or len(phone) < 7:
        await message.reply(
            "❌ Invalid format. Phone must start with `+`.\n"
            "Example: `+919876543210`"
        )
        return

    temp_client = PyroClient(
        name     = "temp_login_session",
        api_id   = API_ID,
        api_hash = API_HASH,
        in_memory = True,
    )

    status_msg = await message.reply(f"📡 Connecting and sending OTP to `{phone}`...")

    try:
        await temp_client.connect()
        code_info = await temp_client.send_code(phone)

        state.update({
            "step":            "waiting_code",
            "phone":           phone,
            "phone_code_hash": code_info.phone_code_hash,
            "temp_client":     temp_client,
            "account_password": "",
        })

        await status_msg.edit_text(
            f"✅ **OTP Sent to** `{phone}`\n\n"
            f"📩 Enter the OTP you received (digits only):\n\n"
            f"_If you didn't receive it, wait 60 seconds and retry /login._"
        )

    except FloodWait as e:
        await temp_client.disconnect()
        await _cleanup_state(admin_id)
        await status_msg.edit_text(
            f"⏳ **Flood Wait!**\n\n"
            f"Telegram is asking us to wait **{e.value} seconds**.\n"
            f"Please try /login again after that time."
        )

    except Exception as e:
        await temp_client.disconnect()
        await _cleanup_state(admin_id)
        await status_msg.edit_text(
            f"❌ **Failed to send OTP**\n\n"
            f"Error: `{str(e)[:300]}`\n\n"
            f"Try /login again."
        )


# ─────────────────────────────────────────────
#  STEP: OTP CODE
# ─────────────────────────────────────────────

async def _step_code(client, message: Message, admin_id: int, state: dict) -> None:
    code         = (message.text or "").strip().replace(" ", "")
    temp_client  = state["temp_client"]
    phone        = state["phone"]
    code_hash    = state["phone_code_hash"]

    try:
        await temp_client.sign_in(phone, code_hash, code)
        # Signed in successfully (no 2FA)
        state["step"] = "waiting_country"
        await message.reply(
            f"✅ **Logged in successfully!**\n\n"
            f"📱 Phone: `{phone}`\n\n"
            f"🌍 **Step 3/5** — Enter the **country name** for this account:\n"
            f"Example: `INDIA` or `USA`"
        )

    except SessionPasswordNeeded:
        state["step"] = "waiting_password"
        await message.reply(
            f"🔐 **2FA Password Required**\n\n"
            f"This account has Two-Factor Authentication enabled.\n"
            f"Enter the 2FA password:"
        )

    except PhoneCodeInvalid:
        await message.reply(
            "❌ **Invalid OTP!**\n\n"
            "The code you entered is wrong. Please check and try again:"
        )

    except PhoneCodeExpired:
        await message.reply(
            "⏰ **OTP Expired!**\n\n"
            "The code has expired. Run /login to start fresh."
        )
        await _cleanup_state(admin_id)

    except FloodWait as e:
        await message.reply(
            f"⏳ **Flood Wait: {e.value}s**\n\n"
            f"Please wait and try /login again."
        )
        await _cleanup_state(admin_id)

    except Exception as e:
        await _notify_failure(client, admin_id, phone, str(e))
        await _cleanup_state(admin_id)


# ─────────────────────────────────────────────
#  STEP: 2FA PASSWORD
# ─────────────────────────────────────────────

async def _step_password(client, message: Message, admin_id: int, state: dict) -> None:
    password    = (message.text or "").strip()
    temp_client = state["temp_client"]
    phone       = state["phone"]

    try:
        await temp_client.check_password(password)
        state.update({"step": "waiting_country", "account_password": password})
        await message.reply(
            f"✅ **2FA Verified!**\n\n"
            f"📱 Phone: `{phone}`\n\n"
            f"🌍 **Step 3/5** — Enter the **country name** for this account:\n"
            f"Example: `INDIA` or `USA`"
        )

    except PasswordHashInvalid:
        await message.reply(
            "❌ **Wrong 2FA Password!**\n\n"
            "Please try again:"
        )

    except FloodWait as e:
        await message.reply(
            f"⏳ **Flood Wait: {e.value}s**\n\n"
            f"Please wait and try /login again."
        )
        await _cleanup_state(admin_id)

    except Exception as e:
        await _notify_failure(client, admin_id, phone, str(e))
        await _cleanup_state(admin_id)


# ─────────────────────────────────────────────
#  STEP: COUNTRY
# ─────────────────────────────────────────────

async def _step_country(client, message: Message, admin_id: int, state: dict) -> None:
    country = (message.text or "").strip().upper()
    if not country:
        await message.reply("❌ Country cannot be empty. Try again:")
        return

    state.update({"step": "waiting_price", "country": country})
    await message.reply(
        f"✅ Country: `{country}`\n\n"
        f"💰 **Step 4/5** — Enter the **selling price** (₹):\n"
        f"Example: `199` or `299.50`"
    )


# ─────────────────────────────────────────────
#  STEP: PRICE → Show automation menu
# ─────────────────────────────────────────────

async def _step_price(client, message: Message, admin_id: int, state: dict) -> None:
    try:
        price = float((message.text or "").strip())
        if price <= 0:
            raise ValueError("Non-positive")
    except ValueError:
        await message.reply("❌ Price must be a positive number. Example: `199`")
        return

    state.update({"step": "waiting_menu_choice", "price": price})
    await _ask_automation_choices(client, admin_id)


# ─────────────────────────────────────────────
#  AUTOMATION CHOICE KEYBOARD
# ─────────────────────────────────────────────

async def _ask_automation_choices(client, admin_id: int) -> None:
    """Send the automation selection menu to admin."""
    state = session_states.get(admin_id, {})
    phone = state.get("phone", "unknown")

    text = (
        f"⚙️ **Step 5/5 — Account Setup**\n\n"
        f"📱 Phone: `{phone}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose what automated setup to run:\n\n"
        f"🔵 **Full Setup** — Leave all groups + block private contacts + change 2FA + set recovery email\n\n"
        f"🟢 **Clean Only** — Leave all groups + block private contacts\n\n"
        f"🟡 **Security Only** — Change 2FA password + set recovery email\n\n"
        f"⚪ **Skip** — Add account to shop immediately (no automation)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 Full Setup",     callback_data="setup_all")],
        [InlineKeyboardButton("🟢 Clean Only",     callback_data="setup_clean")],
        [InlineKeyboardButton("🟡 Security Only",  callback_data="setup_sec")],
        [InlineKeyboardButton("⚪ Skip (Add Now)", callback_data="setup_skip")],
    ])
    await client.send_message(admin_id, text, reply_markup=markup)


# ─────────────────────────────────────────────
#  AUTOMATION CALLBACK HANDLER
# ─────────────────────────────────────────────

async def handle_automation_callback(client, callback: CallbackQuery) -> None:
    """
    Handles setup_all / setup_clean / setup_sec / setup_skip callbacks.
    Builds choices dict and launches _process_automation.
    """
    admin_id = callback.from_user.id
    state    = session_states.get(admin_id)

    if not state or state.get("step") != "waiting_menu_choice":
        await callback.answer("⚠️ No active login session.", show_alert=True)
        return

    # Delete the menu message
    try:
        await callback.message.delete()
    except Exception:
        pass

    data = callback.data

    if data == "setup_skip":
        await callback.answer("⏩ Skipping automation...", show_alert=False)
        await _save_account_to_db(client, admin_id, state)
        return

    if data == "setup_all":
        choices = {"clear_groups": True, "block_contacts": True, "change_2fa": True, "set_email": True}
        label   = "🔵 Full Setup"
    elif data == "setup_clean":
        choices = {"clear_groups": True, "block_contacts": True, "change_2fa": False, "set_email": False}
        label   = "🟢 Clean Only"
    elif data == "setup_sec":
        choices = {"clear_groups": False, "block_contacts": False, "change_2fa": True, "set_email": True}
        label   = "🟡 Security Only"
    else:
        await callback.answer("❓ Unknown option.", show_alert=True)
        return

    await callback.answer(f"✅ {label} selected", show_alert=False)
    state.update({"step": "running", "choices": choices})
    await _process_automation(client, admin_id, state)


# ─────────────────────────────────────────────
#  STEP: NEW 2FA PASSWORD (prompted during automation)
# ─────────────────────────────────────────────

async def _step_new_2fa(client, message: Message, admin_id: int, state: dict) -> None:
    pwd = (message.text or "").strip()
    if len(pwd) < 6:
        await message.reply("❌ Password must be at least **6 characters**. Try again:")
        return

    # Save globally for future accounts too
    update_config("admin_2fa", pwd)
    state["new_2fa_override"] = pwd
    state["step"] = "running"

    await message.reply(f"✅ 2FA password saved: `{pwd}`\n\n⚙️ Continuing automation...")
    await _process_automation(client, admin_id, state)


# ─────────────────────────────────────────────
#  STEP: RECOVERY EMAIL (prompted during automation)
# ─────────────────────────────────────────────

async def _step_recovery_email(client, message: Message, admin_id: int, state: dict) -> None:
    email = (message.text or "").strip()
    if "@" not in email or "." not in email:
        await message.reply("❌ Invalid email format. Example: `user@gmail.com`")
        return

    # Save globally for future accounts
    update_config("recovery_email", email)
    state["recovery_email_input"] = email
    state["step"]                 = "waiting_email_otp"

    await message.reply(
        f"✅ Email saved: `{email}`\n\n"
        f"⚙️ Continuing setup — an email verification code will be triggered.\n"
        f"_You'll be asked for the code if required by Telegram._"
    )
    await _process_automation(client, admin_id, state)


# ─────────────────────────────────────────────
#  STEP: EMAIL OTP (for cloud password email verification)
# ─────────────────────────────────────────────

async def _step_email_otp(client, message: Message, admin_id: int, state: dict) -> None:
    email_code  = (message.text or "").strip()
    temp_client = state.get("temp_client")

    if not temp_client:
        await message.reply("❌ Session lost. Please run /login again.")
        await _cleanup_state(admin_id)
        return

    try:
        # Use the raw Telegram API to confirm the password email code
        import pyrogram.raw.functions.account as account_funcs
        await temp_client.invoke(account_funcs.ConfirmPasswordEmail(code=email_code))
        state["email_verified"] = True
        state["step"]           = "running"
        await message.reply("✅ **Email verified!**\n\n⚙️ Continuing automation...")
        await _process_automation(client, admin_id, state)

    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ["code_invalid", "code_expired", "invalid", "expired", "wrong"]):
            await message.reply(
                "❌ **Invalid or expired email code!**\n\n"
                "Please check your email and enter the code again:"
            )
        else:
            logger.warning(f"⚠️ Email OTP error (non-fatal, continuing without email): {e}")
            state["email_verified"] = False
            state["step"]           = "running"
            await message.reply(
                f"⚠️ Email verification failed: `{str(e)[:100]}`\n\n"
                f"Continuing without recovery email..."
            )
            await _process_automation(client, admin_id, state)


# ─────────────────────────────────────────────
#  CORE AUTOMATION ENGINE
# ─────────────────────────────────────────────

async def _process_automation(client, admin_id: int, state: dict) -> None:
    """
    Main automation engine. Runs sequentially:
      1. Gate checks (get missing passwords/emails before doing anything)
      2. Leave groups
      3. Block private contacts
      4. Change/enable 2FA
      5. Set recovery email
      6. Export session → save to DB → send report
    """
    choices     = state.get("choices", {})
    temp_client = state.get("temp_client")
    phone       = state.get("phone", "unknown")

    # ── Resolve 2FA password ─────────────────────
    cfg      = get_config()
    new_2fa  = (
        state.get("new_2fa_override")
        or cfg.get("admin_2fa")
        or ""
    )
    recovery_email = (
        state.get("recovery_email_input")
        or cfg.get("recovery_email")
        or ""
    )

    # ── Gate check: need 2FA password ────────────
    if choices.get("change_2fa") and not new_2fa:
        state["step"] = "waiting_new_2fa"
        await client.send_message(
            admin_id,
            "🔐 **2FA Password Required**\n\n"
            "No default 2FA password is configured.\n"
            "Enter a new 2FA password (min 6 chars):\n\n"
            "_This will also be saved as the default for future accounts._"
        )
        return

    # ── Gate check: need recovery email ──────────
    if choices.get("set_email") and not recovery_email and state.get("step") != "waiting_email_otp":
        state["step"] = "waiting_recovery_email"
        await client.send_message(
            admin_id,
            "📧 **Recovery Email Required**\n\n"
            "No default recovery email is configured.\n"
            "Enter a recovery email address:\n\n"
            "_This will also be saved as the default for future accounts._"
        )
        return

    # ── Already waiting for email OTP — don't proceed ──
    if state.get("step") == "waiting_email_otp":
        return

    # ── Send status message ───────────────────────
    status_msg = await client.send_message(
        admin_id,
        f"⚙️ **Running Automation for** `{phone}`\n\n"
        f"Please wait — do not send any messages until done...\n\n"
        f"{'🔵 Full Setup' if choices.get('clear_groups') and choices.get('change_2fa') else ''}{'🟢 Clean Only' if choices.get('clear_groups') and not choices.get('change_2fa') else ''}{'🟡 Security Only' if not choices.get('clear_groups') and choices.get('change_2fa') else ''}"
    )

    # Mark account as 'processing' in DB so Heroku restart recovery detects it
    _col("accounts").update_one(
        {"phone": phone},
        {"$set": {
            "phone":    phone,
            "status":   "processing",
            "added_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )

    result_lines      = []
    final_password    = state.get("account_password", "")  # existing password if any
    email_set_ok      = False
    email_otp_needed  = False

    # ────────────────────────────────────────────
    #  TASK 1 — Leave all groups / channels
    # ────────────────────────────────────────────
    if choices.get("clear_groups"):
        left_count = 0
        try:
            await _update_status(status_msg, f"🧹 Leaving groups/channels for `{phone}`...")
            async for dialog in temp_client.get_dialogs():
                if dialog.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                    try:
                        await temp_client.leave_chat(dialog.chat.id, delete=True)
                        left_count += 1
                        await asyncio.sleep(0.4)
                    except Exception as e:
                        logger.debug(f"Leave chat error (non-fatal): {e}")
            result_lines.append(f"✅ Left **{left_count}** group(s)/channel(s)")
        except Exception as e:
            result_lines.append(f"⚠️ Group cleanup partial: `{str(e)[:100]}`")

    # ────────────────────────────────────────────
    #  TASK 2 — Block private contacts
    # ────────────────────────────────────────────
    if choices.get("block_contacts"):
        blocked_count = 0
        try:
            await _update_status(status_msg, f"🚫 Blocking private contacts for `{phone}`...")
            async for dialog in temp_client.get_dialogs():
                if dialog.chat.type in (ChatType.PRIVATE, ChatType.BOT):
                    cid = dialog.chat.id
                    if cid == 777000:
                        continue  # Never block Telegram's service account
                    try:
                        await temp_client.delete_chat_history(cid, revoke=True)
                        await temp_client.block_user(cid)
                        blocked_count += 1
                        await asyncio.sleep(0.4)
                    except Exception as e:
                        logger.debug(f"Block user error (non-fatal): {e}")
            result_lines.append(f"✅ Blocked **{blocked_count}** private contact(s)")
        except Exception as e:
            result_lines.append(f"⚠️ Contact cleanup partial: `{str(e)[:100]}`")

    # ────────────────────────────────────────────
    #  TASK 3 — 2FA change or enable
    # ────────────────────────────────────────────
    if choices.get("change_2fa") and new_2fa:
        existing_pwd = state.get("account_password", "")
        try:
            await _update_status(status_msg, f"🔐 Setting 2FA password for `{phone}`...")

            if existing_pwd:
                # Account already has 2FA — change it
                await temp_client.change_cloud_password(
                    current_password = existing_pwd,
                    new_password     = new_2fa,
                    new_hint         = "OTP Ocean",
                )
                final_password = new_2fa
                result_lines.append(f"✅ 2FA password **changed** successfully")

            elif recovery_email and choices.get("set_email"):
                # Enable 2FA with email (Telegram will send verification email)
                try:
                    await temp_client.enable_cloud_password(
                        password = new_2fa,
                        hint     = "OTP Ocean",
                        email    = recovery_email,
                    )
                    final_password = new_2fa
                    result_lines.append(f"✅ 2FA **enabled** (with email verification pending)")
                    # Telegram requires the email to be confirmed
                    email_otp_needed       = True
                    state["step"]          = "waiting_email_otp"
                    state["result_lines"]  = result_lines
                    state["final_password"] = final_password

                    await _update_status(
                        status_msg,
                        f"📧 **Email verification required!**\n\n"
                        f"Telegram sent a code to `{recovery_email}`.\n"
                        f"Enter that code now:"
                    )
                    return  # Wait for email OTP

                except Exception as e:
                    if "email" in str(e).lower():
                        result_lines.append(f"⚠️ Email 2FA error: `{str(e)[:100]}`")
                    else:
                        raise

            else:
                # Enable 2FA without email
                await temp_client.enable_cloud_password(
                    password = new_2fa,
                    hint     = "OTP Ocean",
                )
                final_password = new_2fa
                result_lines.append(f"✅ 2FA **enabled** successfully")

        except Exception as e:
            result_lines.append(f"⚠️ 2FA setup error: `{str(e)[:100]}`")

    # ────────────────────────────────────────────
    #  TASK 4 — Set recovery email (if not done via enable_cloud_password)
    # ────────────────────────────────────────────
    if choices.get("set_email") and recovery_email and not email_otp_needed:
        if state.get("email_verified"):
            result_lines.append(f"✅ Recovery email **verified**: `{recovery_email}`")
            email_set_ok = True
        elif not choices.get("change_2fa"):
            # 2FA was already set, just add email
            try:
                await _update_status(status_msg, f"📧 Setting recovery email for `{phone}`...")
                pwd_to_use = final_password or state.get("account_password", "")
                if pwd_to_use:
                    # Remove then re-enable with email to attach email
                    await temp_client.remove_cloud_password(pwd_to_use)
                    await temp_client.enable_cloud_password(
                        password = pwd_to_use,
                        hint     = "OTP Ocean",
                        email    = recovery_email,
                    )
                    state["step"]         = "waiting_email_otp"
                    state["result_lines"] = result_lines
                    state["final_password"] = pwd_to_use
                    await _update_status(
                        status_msg,
                        f"📧 **Email verification required!**\n\n"
                        f"Telegram sent a code to `{recovery_email}`.\n"
                        f"Enter that code now:"
                    )
                    return  # Wait for email OTP
                else:
                    result_lines.append(f"⚠️ Cannot set email — no 2FA password available")
            except Exception as e:
                result_lines.append(f"⚠️ Email setup error: `{str(e)[:100]}`")
        else:
            result_lines.append(f"ℹ️ Recovery email will be set after 2FA verification")

    # ────────────────────────────────────────────
    #  FINALISE — Export session and save
    # ────────────────────────────────────────────
    # Restore result_lines if resuming after email OTP
    if state.get("result_lines"):
        result_lines = state["result_lines"]
    if state.get("final_password"):
        final_password = state["final_password"]

    verified_email = recovery_email if (state.get("email_verified") or email_set_ok) else ""

    await _finalise_and_save(
        client, admin_id, state, temp_client,
        phone, result_lines, final_password, verified_email, status_msg
    )


# ─────────────────────────────────────────────
#  FINALISE AND SAVE
# ─────────────────────────────────────────────

async def _finalise_and_save(
    client, admin_id: int, state: dict, temp_client,
    phone: str, result_lines: list, final_password: str,
    verified_email: str, status_msg
) -> None:
    """Export session string, save to DB, send summary report."""
    country = state.get("country", "UNKNOWN")
    price   = state.get("price", 0.0)

    try:
        session_string = await temp_client.export_session_string()
        await temp_client.disconnect()
    except Exception as e:
        logger.error(f"❌ Failed to export session for {phone}: {e}")
        try:
            await temp_client.disconnect()
        except Exception:
            pass
        await _cleanup_state(admin_id)
        await client.send_message(
            admin_id,
            f"❌ **Fatal Error: Could not export session!**\n\n"
            f"Phone: `{phone}`\n"
            f"Error: `{str(e)[:200]}`\n\n"
            f"Run /login to try again."
        )
        return

    # Save to accounts collection (overwrites 'processing' status)
    add_account(
        phone          = phone,
        session_string = session_string,
        country        = country,
        price          = price,
        password       = final_password,
        recovery_email = verified_email,
    )

    # Build and send summary report
    task_summary = "\n".join(result_lines) if result_lines else "_No automation tasks ran._"

    report = (
        f"🎉 **Account Added Successfully!**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 **Phone:** `{phone}`\n"
        f"🌍 **Country:** {country}\n"
        f"💰 **Price:** ₹{price:.2f}\n"
        f"🔐 **2FA Password:** `{final_password or 'not set'}`\n"
        f"📧 **Recovery Email:** `{verified_email or 'not set'}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ **Automation Results:**\n{task_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Account is now **live in the shop!**\n"
        f"📅 Added: {_now_ist()}"
    )

    try:
        await _update_status(status_msg, report)
    except Exception:
        await client.send_message(admin_id, report)

    # Log to LOG_GROUP
    try:
        await client.send_message(
            LOG_GROUP,
            f"➕ **NEW ACCOUNT ADDED**\n\n"
            f"📱 **Phone:** `{phone}`\n"
            f"🌍 **Country:** {country}\n"
            f"💰 **Price:** ₹{price:.2f}\n"
            f"👤 **Added by Admin:** `{admin_id}`\n"
            f"📅 **Time:** {_now_ist()}"
        )
    except Exception as e:
        logger.warning(f"⚠️ Could not log to LOG_GROUP: {e}")

    await _cleanup_state(admin_id)


# ─────────────────────────────────────────────
#  SKIP — Direct DB save without automation
# ─────────────────────────────────────────────

async def _save_account_to_db(client, admin_id: int, state: dict) -> None:
    """Directly export and save without any automation."""
    temp_client    = state.get("temp_client")
    phone          = state.get("phone", "unknown")
    country        = state.get("country", "UNKNOWN")
    price          = state.get("price", 0.0)
    account_pwd    = state.get("account_password", "")

    cfg = get_config()

    try:
        session_string = await temp_client.export_session_string()
        await temp_client.disconnect()
    except Exception as e:
        await _cleanup_state(admin_id)
        await client.send_message(
            admin_id,
            f"❌ **Failed to export session!**\n\nError: `{str(e)[:200]}`\n\nTry /login again."
        )
        return

    add_account(
        phone          = phone,
        session_string = session_string,
        country        = country,
        price          = price,
        password       = account_pwd or cfg.get("admin_2fa") or "",
        recovery_email = cfg.get("recovery_email") or "",
    )

    await client.send_message(
        admin_id,
        f"⏩ **Account Added (No Automation)**\n\n"
        f"📱 **Phone:** `{phone}`\n"
        f"🌍 **Country:** {country}\n"
        f"💰 **Price:** ₹{price:.2f}\n\n"
        f"✅ Account is now **live in the shop!**"
    )

    try:
        await client.send_message(
            LOG_GROUP,
            f"➕ **NEW ACCOUNT ADDED (SKIP)**\n\n"
            f"📱 Phone: `{phone}` | 🌍 {country} | 💰 ₹{price:.2f}\n"
            f"👤 Admin: `{admin_id}` | 📅 {_now_ist()}"
        )
    except Exception:
        pass

    await _cleanup_state(admin_id)


# ─────────────────────────────────────────────
#  HELPER: Edit or send new status message
# ─────────────────────────────────────────────

async def _update_status(msg, text: str) -> None:
    """Try to edit the status message; silently fail if not possible."""
    try:
        await msg.edit_text(text)
    except Exception:
        pass
