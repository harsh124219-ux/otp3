"""
handlers/user.py — All user-facing menus and the deposit flow state machine.
Navigation rule: every button tap deletes the old message and sends a new one.
"""

import logging
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
)

from info import START_MESSAGE, RULES_TEXT, SUPPORT_TEXT, PROFILE_TEXT, HELP_TEXT, USER_HELP, ADMIN_HELP, DEPOSIT_TEXT, NO_UPI_TEXT
from database import (
    get_user,
    get_balance,
    get_user_orders,
    add_transaction,
    utr_exists,
    is_admin,
    get_config,
)

logger = logging.getLogger(__name__)

# ── Multi-step deposit state: {user_id: {"step": str, ...}} ──
user_states: dict = {}


# ─────────────────────────────────────────────
#  HELPER: send new message, delete old
# ─────────────────────────────────────────────

async def _send_new(client, chat_id: int, prev_msg, text: str, markup=None, photo=None):
    """Delete previous message (silently) and send a brand new one."""
    try:
        await prev_msg.delete()
    except Exception:
        pass
    if photo:
        return await client.send_photo(chat_id, photo, caption=text, reply_markup=markup)
    return await client.send_message(chat_id, text, reply_markup=markup)


def _get_prev_msg(update):
    """Extract the message object to delete from either Message or CallbackQuery."""
    if isinstance(update, CallbackQuery):
        return update.message
    return update


def _extract_user(update):
    """Extract user info from Message or CallbackQuery."""
    if isinstance(update, CallbackQuery):
        return update.from_user
    return update.from_user


# ─────────────────────────────────────────────
#  MAIN MENU
# ─────────────────────────────────────────────

def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Shop",      callback_data="open_shop"),
            InlineKeyboardButton("💵 Deposit",   callback_data="open_deposit"),
        ],
        [
            InlineKeyboardButton("👤 Profile",   callback_data="open_profile"),
            InlineKeyboardButton("📦 My Orders", callback_data="open_orders"),
        ],
        [
            InlineKeyboardButton("🛟 Support",   callback_data="open_support"),
            InlineKeyboardButton("📋 Rules",     callback_data="open_rules"),
        ],
        [
            InlineKeyboardButton("📖 Help",      callback_data="open_help"),
            InlineKeyboardButton("💰 Balance",   callback_data="open_balance"),
        ],
    ])


async def start(client, update):
    """
    Main menu handler. Accepts both /start command (Message)
    and back_to_main callback (CallbackQuery).
    """
    user = _extract_user(update)
    get_user(user.id, username=user.username, first_name=user.first_name)

    text = START_MESSAGE.format(name=user.first_name)
    prev = _get_prev_msg(update)

    if isinstance(update, CallbackQuery):
        await _send_new(client, user.id, prev, text, markup=_main_keyboard())
    else:
        await update.reply(text, reply_markup=_main_keyboard())


# ─────────────────────────────────────────────
#  BALANCE (command — plain reply, no nav)
# ─────────────────────────────────────────────

async def balance_cmd(client, message: Message):
    """Simple /balance command reply — doesn't delete any message."""
    user_id = message.from_user.id
    bal = get_balance(user_id)
    await message.reply(f"💰 **Your Wallet Balance:** ₹{bal:.2f}")


# ─────────────────────────────────────────────
#  PROFILE
# ─────────────────────────────────────────────

async def profile_menu(client, update):
    user = _extract_user(update)
    doc    = get_user(user.id, username=user.username, first_name=user.first_name)
    orders = get_user_orders(user.id)

    text = PROFILE_TEXT.format(
        name=user.first_name,
        user_id=user.id,
        balance=f"{doc.get('balance', 0.0):.2f}",
        total_spent=f"{doc.get('total_spent', 0.0):.2f}",
        total_purchases=len(orders),
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Deposit Funds", callback_data="open_deposit")],
        [InlineKeyboardButton("🔙 Back",           callback_data="back_to_main")],
    ])

    prev = _get_prev_msg(update)
    if isinstance(update, CallbackQuery):
        await _send_new(client, user.id, prev, text, markup=markup)
    else:
        await update.reply(text, reply_markup=markup)


# ─────────────────────────────────────────────
#  DEPOSIT MENU (Step 0 — show UPI details)
# ─────────────────────────────────────────────

async def deposit_menu(client, update):
    user = _extract_user(update)
    cfg  = get_config()

    upi_id    = cfg.get("upi_id")
    upi_name  = cfg.get("upi_name")
    qr_img    = cfg.get("upi_image_file_id")
    prev      = _get_prev_msg(update)

    if not upi_id:
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
        ])
        if isinstance(update, CallbackQuery):
            await _send_new(client, user.id, prev, NO_UPI_TEXT, markup=markup)
        else:
            await update.reply(NO_UPI_TEXT, reply_markup=markup)
        return

    text = DEPOSIT_TEXT.format(upi_id=upi_id, upi_name=upi_name or "OTP Ocean")
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_deposit")]
    ])

    # Start the deposit state machine
    user_states[user.id] = {"step": "waiting_amount"}

    if isinstance(update, CallbackQuery):
        if qr_img:
            await _send_new(client, user.id, prev, text, markup=markup, photo=qr_img)
        else:
            await _send_new(client, user.id, prev, text, markup=markup)
    else:
        if qr_img:
            await client.send_photo(user.id, qr_img, caption=text, reply_markup=markup)
        else:
            await update.reply(text, reply_markup=markup)


# ─────────────────────────────────────────────
#  ORDERS MENU
# ─────────────────────────────────────────────

def _mask_number(phone: str) -> str:
    """Mask middle digits of a phone number. +919876543210 → +91*****0"""
    phone = phone.strip()
    if len(phone) <= 4:
        return phone
    # Keep first 3 chars (+CC), mask middle, show last 1
    prefix = phone[:3]
    suffix = phone[-1]
    mask   = "*" * (len(phone) - 4)
    return f"{prefix}{mask}{suffix}"


async def orders_menu(client, update):
    user   = _extract_user(update)
    orders = get_user_orders(user.id)
    prev   = _get_prev_msg(update)

    if not orders:
        text = (
            "📦 **My Orders**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "❌ You have no orders yet.\n\n"
            "👉 Head to the Shop to buy your first account!"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Go to Shop", callback_data="open_shop")],
            [InlineKeyboardButton("🔙 Back",        callback_data="back_to_main")],
        ])
        if isinstance(update, CallbackQuery):
            await _send_new(client, user.id, prev, text, markup=markup)
        else:
            await update.reply(text, reply_markup=markup)
        return

    text = (
        "📦 **My Orders**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Tap an order to fetch its OTP code.\n"
        "✅ = Active   🔒 = Closed"
    )

    buttons = []
    for order in orders[:10]:  # Show last 10 orders
        status_icon = "✅" if order["status"] == "active" else "🔒"
        label = f"{status_icon} {order['country']} — {_mask_number(order['phone'])}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"get_otp_{order['order_id']}")])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    markup = InlineKeyboardMarkup(buttons)

    if isinstance(update, CallbackQuery):
        await _send_new(client, user.id, prev, text, markup=markup)
    else:
        await update.reply(text, reply_markup=markup)


# ─────────────────────────────────────────────
#  HELP MENU
# ─────────────────────────────────────────────

async def help_menu(client, update):
    user   = _extract_user(update)
    prev   = _get_prev_msg(update)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 User Commands",   callback_data="help_user")],
        [InlineKeyboardButton("🔐 Admin Commands",  callback_data="help_admin")],
        [InlineKeyboardButton("🔙 Back",            callback_data="back_to_main")],
    ])

    if isinstance(update, CallbackQuery):
        await _send_new(client, user.id, prev, HELP_TEXT, markup=markup)
    else:
        await update.reply(HELP_TEXT, reply_markup=markup)


async def help_detail(client, callback: CallbackQuery):
    """Show user or admin help text based on which button was tapped."""
    user_id = callback.from_user.id

    if callback.data == "help_admin":
        if not is_admin(user_id):
            await callback.answer("❌ Admin only!", show_alert=True)
            return
        text = ADMIN_HELP
    else:
        text = USER_HELP

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Help", callback_data="open_help")]
    ])

    await _send_new(client, user_id, callback.message, text, markup=markup)


# ─────────────────────────────────────────────
#  DEPOSIT FLOW STATE MACHINE (message handler)
# ─────────────────────────────────────────────

async def handle_message(client, message: Message):
    """
    Handles all non-command private messages for user deposit flow.
    Called from main.py AFTER admin state checks.
    """
    user_id = message.from_user.id
    state   = user_states.get(user_id)

    if not state:
        return  # User hasn't started any flow — silently ignore

    step = state.get("step")

    # ── Step 1: Amount ──────────────────────────────────
    if step == "waiting_amount":
        text_raw = (message.text or "").strip()
        try:
            amount = float(text_raw)
            if amount <= 0:
                raise ValueError("Non-positive amount")
        except ValueError:
            await message.reply(
                "❌ **Invalid amount!**\n\n"
                "Please enter a positive number.\n"
                "Example: `200` or `499.50`"
            )
            return

        user_states[user_id] = {"step": "waiting_ss", "amount": amount}
        await message.reply(
            f"✅ **Amount set:** ₹{amount:.2f}\n\n"
            f"📸 Now send your **payment screenshot** as a photo."
        )

    # ── Step 2: Screenshot ──────────────────────────────
    elif step == "waiting_ss":
        if not message.photo:
            await message.reply(
                "📸 **Please send your payment screenshot as a photo!**\n\n"
                "_(Not as a file/document — send as a normal photo)_"
            )
            return

        file_id = message.photo.file_id
        user_states[user_id].update({"step": "waiting_utr", "ss_file_id": file_id})
        await message.reply(
            "✅ **Screenshot received!**\n\n"
            "🔢 Now send your **UTR / Transaction ID**\n"
            "_(12–22 digits — found in your UPI app's transaction details)_"
        )

    # ── Step 3: UTR ────────────────────────────────────
    elif step == "waiting_utr":
        utr = (message.text or "").strip()

        if not utr.isdigit() or not (12 <= len(utr) <= 22):
            await message.reply(
                "⚠️ **Invalid UTR Format!**\n\n"
                "UTR must be **12–22 digits only** (no spaces or letters).\n"
                "Example: `426812345678`"
            )
            return

        if utr_exists(utr):
            await message.reply(
                "❌ **This UTR has already been submitted!**\n\n"
                "If you believe this is an error, contact @OTPOceanSupportBot"
            )
            return

        # All valid — save transaction
        amount     = state["amount"]
        ss_file_id = state["ss_file_id"]

        add_transaction(user_id, utr, amount, ss_file_id)

        # Build log message for LOG_GROUP
        from info import LOG_GROUP
        user = message.from_user
        username_str = f"@{user.username}" if user.username else "_(no username)_"

        log_caption = (
            "💸 **NEW PAYMENT REQUEST**\n\n"
            f"👤 **User:** {user.first_name} ({username_str})\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"💰 **Amount:** ₹{amount:.2f}\n"
            f"🔢 **UTR:** `{utr}`\n"
            f"📅 **Time:** {_now_ist()}"
        )

        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        log_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"approve_{utr}_{user_id}_{amount}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"reject_{utr}_{user_id}"
                ),
            ]
        ])

        try:
            await client.send_photo(
                LOG_GROUP,
                ss_file_id,
                caption=log_caption,
                reply_markup=log_markup,
            )
        except Exception as e:
            logger.error(f"❌ Failed to send payment log to LOG_GROUP: {e}")

        # Confirm to user
        await message.reply(
            "✅ **Payment Submitted Successfully!**\n\n"
            f"💰 **Amount:** ₹{amount:.2f}\n"
            f"🔢 **UTR:** `{utr}`\n\n"
            "⏳ You'll be notified once your payment is approved.\n"
            "_(Usually within 5–30 minutes)_"
        )

        user_states.pop(user_id, None)


def _now_ist() -> str:
    """Return current time formatted as readable string."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")
