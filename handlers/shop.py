"""
handlers/shop.py — Shop browsing, account purchase (atomic), OTP fetch, session close.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from database import (
    get_all_countries,
    get_accounts_by_country_sorted,
    get_balance,
    deduct_balance,
    add_balance,
    update_account_status,
    create_order,
    get_order,
    close_order,
    get_config,
    get_account,
    _col,
)
from info import API_ID, API_HASH, LOG_GROUP

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  HELPER: delete old message, send new
# ─────────────────────────────────────────────

async def _replace(client, chat_id: int, old_msg, text: str, markup=None):
    try:
        await old_msg.delete()
    except Exception:
        pass
    return await client.send_message(chat_id, text, reply_markup=markup)


def _mask_number(phone: str) -> str:
    """Mask middle digits of phone. +919876543210 → +91*****0"""
    phone = phone.strip()
    if len(phone) <= 4:
        return phone
    prefix = phone[:3]
    suffix = phone[-1]
    mask   = "*" * (len(phone) - 4)
    return f"{prefix}{mask}{suffix}"


def _now_str() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


# ─────────────────────────────────────────────
#  SHOP MAIN MENU
# ─────────────────────────────────────────────

async def shop_menu(client, update):
    """Show all countries with available account counts."""
    from pyrogram.types import CallbackQuery
    chat_id = update.from_user.id

    if isinstance(update, CallbackQuery):
        old_msg = update.message
    else:
        old_msg = update

    countries = get_all_countries()

    if not countries:
        text = (
            "🛒 **OTP Ocean Shop**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "😔 No accounts available at the moment.\n"
            "Check back soon — we restock regularly!"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
        ])
        await _replace(client, chat_id, old_msg, text, markup)
        return

    # Count available accounts per country
    buttons = []
    for country in sorted(countries):
        count = _col("accounts").count_documents(
            {"status": "available", "country": country}
        )
        if count > 0:
            label = f"🌍 {country}  ✅ ({count} available)"
        else:
            label = f"🌍 {country}  ❌ (out of stock)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"sort_opts_{country}")])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])

    text = (
        "🛒 **OTP Ocean Shop**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select a country to browse available accounts.\n\n"
        f"💰 Your balance: ₹{get_balance(chat_id):.2f}"
    )
    await _replace(client, chat_id, old_msg, text, InlineKeyboardMarkup(buttons))


# ─────────────────────────────────────────────
#  SORT OPTIONS MENU
# ─────────────────────────────────────────────

async def sort_options_menu(client, callback: CallbackQuery):
    """Show sort order selection for a country."""
    country = callback.data.replace("sort_opts_", "")
    chat_id = callback.from_user.id

    text = (
        f"🌍 **{country}**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "How would you like accounts sorted?"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 Cheapest First",       callback_data=f"view_country_{country}_low")],
        [InlineKeyboardButton("📈 Most Expensive First", callback_data=f"view_country_{country}_high")],
        [InlineKeyboardButton("🔙 Back to Shop",         callback_data="open_shop")],
    ])
    await _replace(client, chat_id, callback.message, text, markup)


# ─────────────────────────────────────────────
#  VIEW COUNTRY ACCOUNTS LIST
# ─────────────────────────────────────────────

async def view_country_accounts(client, callback: CallbackQuery):
    """List available accounts for the chosen country and sort order."""
    chat_id = callback.from_user.id
    # Callback format: view_country_{COUNTRY}_{low|high}
    # Country may contain underscores so split from the right
    parts      = callback.data.split("_")
    sort_key   = parts[-1]                     # "low" or "high"
    country    = "_".join(parts[2:-1])         # everything between view_country_ and _low/high
    sort_order = "low_to_high" if sort_key == "low" else "high_to_low"

    accounts = get_accounts_by_country_sorted(country, sort_order)

    if not accounts:
        await callback.answer("❌ Out of stock for this country!", show_alert=True)
        await shop_menu(client, callback)
        return

    balance = get_balance(chat_id)

    buttons = []
    for acc in accounts:
        affordable = "✅" if balance >= acc["price"] else "💸"
        label = f"{affordable}  ₹{acc['price']:.0f}  —  {_mask_number(acc['phone'])}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"buy_acc_{acc['phone']}")])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"sort_opts_{country}")])

    icon = "📉" if sort_order == "low_to_high" else "📈"
    text = (
        f"🌍 **{country}** — {icon} {'Cheapest First' if sort_order == 'low_to_high' else 'Most Expensive First'}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Your balance: ₹{balance:.2f}\n"
        f"✅ = Affordable  💸 = Need more balance\n\n"
        f"Tap an account to purchase it:"
    )
    await _replace(client, chat_id, callback.message, text, InlineKeyboardMarkup(buttons))


# ─────────────────────────────────────────────
#  BUY ACCOUNT (atomic)
# ─────────────────────────────────────────────

async def buy_account(client, callback: CallbackQuery):
    """
    Atomic purchase flow:
    1. Find account (status=available)
    2. Check balance
    3. Deduct balance
    4. Atomic status update (modified_count check)
    5. Refund on race condition
    6. Create order, notify LOG_GROUP
    """
    chat_id  = callback.from_user.id
    user     = callback.from_user
    phone    = callback.data.replace("buy_acc_", "")

    # 1. Find the account
    account = _col("accounts").find_one({"phone": phone, "status": "available"})
    if not account:
        await callback.answer("❌ This account was just sold to someone else!", show_alert=True)
        await shop_menu(client, callback)
        return

    price = account["price"]

    # 2. Check balance
    if get_balance(chat_id) < price:
        await callback.answer(
            f"❌ Insufficient balance!\nYou need ₹{price:.2f} but have ₹{get_balance(chat_id):.2f}",
            show_alert=True
        )
        return

    # 3. Deduct balance
    if not deduct_balance(chat_id, price):
        await callback.answer("❌ Payment error. Please try again.", show_alert=True)
        return

    # 4. Atomic status flip — only succeeds if still "available"
    result = _col("accounts").update_one(
        {"phone": phone, "status": "available"},
        {"$set": {"status": "sold"}}
    )

    if result.modified_count == 0:
        # Race condition — refund immediately
        add_balance(chat_id, price)
        await callback.answer(
            "❌ Sold a split-second before you! Your balance has been refunded.",
            show_alert=True
        )
        await shop_menu(client, callback)
        return

    # 5. Create order (copies session_string at purchase time)
    order_id = create_order(
        user_id        = chat_id,
        phone          = phone,
        session_string = account["session_string"],
        country        = account["country"],
        price          = price,
    )

    new_balance = get_balance(chat_id)

    # 6. Log to LOG_GROUP
    username_str = f"@{user.username}" if user.username else "_(no username)_"
    log_text = (
        "🛒 **NEW SALE**\n\n"
        f"👤 **Buyer:** {user.first_name} ({username_str})\n"
        f"🆔 **User ID:** `{chat_id}`\n"
        f"📱 **Phone:** `{phone}`\n"
        f"🌍 **Country:** {account['country']}\n"
        f"💰 **Price:** ₹{price:.2f}\n"
        f"🧾 **Order ID:** `{order_id}`\n"
        f"📅 **Time:** {_now_str()}"
    )
    try:
        await client.send_message(LOG_GROUP, log_text)
    except Exception as e:
        logger.error(f"❌ Failed to send sale log: {e}")

    # 7. Show success to buyer
    text = (
        "🎉 **Purchase Successful!**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 **Number:** `{phone}`\n"
        f"🌍 **Country:** {account['country']}\n"
        f"💰 **Paid:** ₹{price:.2f}\n"
        f"🧾 **Order ID:** `{order_id}`\n"
        f"💳 **Remaining Balance:** ₹{new_balance:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📦 Go to **My Orders** to fetch your OTP code!\n"
        "⚠️ _Change your 2FA password after first login._"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 My Orders",  callback_data="open_orders")],
        [InlineKeyboardButton("🛒 Buy Another", callback_data="open_shop")],
        [InlineKeyboardButton("🏠 Main Menu",   callback_data="back_to_main")],
    ])
    await _replace(client, chat_id, callback.message, text, markup)


# ─────────────────────────────────────────────
#  OTP FETCH
# ─────────────────────────────────────────────

async def get_otp_logic(client, callback: CallbackQuery):
    """
    Connect to the purchased account using its session string,
    fetch recent messages from 777000 (Telegram OTP sender),
    extract any 5–6 digit OTP from the last 2 minutes.
    Does NOT delete the orders list message.
    """
    from pyrogram import Client as PyroClient

    chat_id  = callback.from_user.id
    order_id = callback.data.replace("get_otp_", "")

    order = get_order(order_id)
    if not order:
        await callback.answer("❌ Order not found!", show_alert=True)
        return

    if order["status"] == "closed":
        await callback.answer("🔒 This session has been closed.", show_alert=True)
        return

    if not order.get("session_string"):
        await callback.answer("❌ Session unavailable — account may have been closed.", show_alert=True)
        return

    await callback.answer("⏳ Connecting to account...", show_alert=False)

    phone      = order["phone"]
    otp_text   = None
    full_msg   = None

    user_client = None
    try:
        user_client = PyroClient(
            name           = f"otp_{order_id}",
            api_id         = API_ID,
            api_hash       = API_HASH,
            session_string = order["session_string"],
            in_memory      = True,
            no_updates     = True,
        )
        await user_client.connect()

        # Fetch last 20 messages from 777000 (Telegram service account)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
        otp_pattern = re.compile(r'\b\d{5,6}\b')

        async for msg in user_client.get_chat_history(777000, limit=20):
            if not msg.date:
                continue
            msg_date = msg.date
            # Ensure timezone-aware comparison
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            if msg_date >= cutoff:
                raw_text = msg.text or msg.caption or ""
                match = otp_pattern.search(raw_text)
                if match:
                    otp_text = match.group()
                    full_msg = raw_text
                    break

    except Exception as e:
        logger.error(f"❌ OTP fetch error for order {order_id}: {e}")
        await client.send_message(
            chat_id,
            f"⚠️ **Connection Error**\n\n"
            f"Could not connect to the account: `{phone}`\n"
            f"Error: `{str(e)[:200]}`\n\n"
            f"Try again in a moment. If this persists, contact support.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Retry", callback_data=f"get_otp_{order_id}")],
                [InlineKeyboardButton("🔙 My Orders", callback_data="open_orders")],
            ])
        )
        return
    finally:
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass

    # Build OTP message (sent as NEW message — does NOT delete orders list)
    if otp_text and full_msg:
        otp_message = (
            f"📩 **OTP for** `{phone}`\n"
            f"_(last 2 minutes)_\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔐 **Code:** `{otp_text}`\n\n"
            f"📨 **Full Message:**\n`{full_msg}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        otp_message = (
            f"📩 **OTP for** `{phone}`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "❌ **No fresh OTP found** in the last 2 minutes.\n\n"
            "👉 **Steps:**\n"
            "1. Open Telegram and try logging in with this number\n"
            "2. Wait for the OTP to be sent\n"
            "3. Tap 🔄 Refresh below\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    otp_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh OTP",          callback_data=f"get_otp_{order_id}")],
        [InlineKeyboardButton("🚪 Close Session ⚠️",     callback_data=f"logout_confirm_{order_id}")],
        [InlineKeyboardButton("🔙 Back to Orders",       callback_data="open_orders")],
    ])

    await client.send_message(chat_id, otp_message, reply_markup=otp_markup)

    # Send 2FA and recovery email as separate messages if set
    account_data = get_account(phone)
    if account_data:
        extras = []
        if account_data.get("password"):
            extras.append(f"🔐 **2FA Password:** `{account_data['password']}`")
        if account_data.get("recovery_email"):
            extras.append(f"📧 **Recovery Email:** `{account_data['recovery_email']}`")
        if extras:
            await client.send_message(
                chat_id,
                "🔑 **Account Credentials**\n\n" + "\n".join(extras) +
                "\n\n⚠️ _Change these after first login!_"
            )


# ─────────────────────────────────────────────
#  LOGOUT — Two-step confirmation
# ─────────────────────────────────────────────

async def logout_acc_logic(client, callback: CallbackQuery):
    """
    Handles both steps of session close:
    - logout_confirm_{order_id} → show warning
    - logout_acc_{order_id}     → actually close
    """
    chat_id = callback.from_user.id
    data    = callback.data

    # ── STEP 1: Warning screen ──────────────────────
    if data.startswith("logout_confirm_"):
        order_id = data.replace("logout_confirm_", "")
        order = get_order(order_id)
        if not order:
            await callback.answer("❌ Order not found!", show_alert=True)
            return

        text = (
            "⚠️ **PERMANENT ACTION WARNING**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 **Number:** `{order['phone']}`\n"
            f"🌍 **Country:** {order['country']}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔴 If you close this session:\n"
            "• You will **NEVER** be able to fetch OTP from this account again\n"
            "• The session string will be permanently deleted\n"
            "• This action **cannot be undone**\n\n"
            "Are you absolutely sure?"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Close Session", callback_data=f"logout_acc_{order_id}")],
            [InlineKeyboardButton("❌ Cancel",              callback_data="open_orders")],
        ])
        await _replace(client, chat_id, callback.message, text, markup)

    # ── STEP 2: Actually close ──────────────────────
    elif data.startswith("logout_acc_"):
        order_id = data.replace("logout_acc_", "")
        order = get_order(order_id)
        if not order:
            await callback.answer("❌ Order not found!", show_alert=True)
            return

        # Close order and clear session strings (from both order and accounts collection)
        close_order(order_id)

        # Log to LOG_GROUP
        log_text = (
            "🚪 **SESSION CLOSED**\n\n"
            f"👤 **User ID:** `{chat_id}`\n"
            f"📱 **Phone:** `{order['phone']}`\n"
            f"🌍 **Country:** {order['country']}\n"
            f"🧾 **Order ID:** `{order_id}`\n"
            f"📅 **Time:** {_now_str()}"
        )
        try:
            await client.send_message(LOG_GROUP, log_text)
        except Exception as e:
            logger.error(f"❌ Failed to send logout log: {e}")

        text = (
            "✅ **Session Closed Successfully**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 **Number:** `{order['phone']}`\n"
            f"🌍 **Country:** {order['country']}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "The session has been permanently terminated.\n"
            "The order is now marked as **closed**."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 My Orders", callback_data="open_orders")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
        ])
        await _replace(client, chat_id, callback.message, text, markup)
