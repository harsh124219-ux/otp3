"""
handlers/admin.py — All admin commands: stats, addbal, broadcast,
manage admins, configure UPI, FSub, and the interactive /addacc flow.
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta

from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
)

from database import (
    is_admin,
    get_config,
    update_config,
    add_admin,
    remove_admin,
    add_fsub_channel,
    remove_fsub_channel,
    get_fsub_channels,
    add_balance,
    get_balance,
    add_account,
    get_all_user_ids,
    _col,
)
from info import ADMIN_ID

logger = logging.getLogger(__name__)

# ── Admin flow state: {admin_id: {"step": str, ...}} ──
admin_states: dict = {}


def _now_ist() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


# ─────────────────────────────────────────────
#  STATS
# ─────────────────────────────────────────────

async def stats(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    total_users    = _col("users").count_documents({})
    total_txns     = _col("transactions").count_documents({})
    pending_txns   = _col("transactions").count_documents({"status": "pending"})
    approved_txns  = _col("transactions").count_documents({"status": "approved"})
    rejected_txns  = _col("transactions").count_documents({"status": "rejected"})
    avail_accounts = _col("accounts").count_documents({"status": "available"})
    sold_accounts  = _col("accounts").count_documents({"status": "sold"})
    total_orders   = _col("orders").count_documents({})
    active_orders  = _col("orders").count_documents({"status": "active"})

    # Total revenue
    pipeline = [
        {"$match": {"status": "approved"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    rev_result = list(_col("transactions").aggregate(pipeline))
    total_revenue = rev_result[0]["total"] if rev_result else 0.0

    await message.reply(
        "📊 **Bot Statistics**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **Total Users:**       {total_users}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💸 **Transactions:**      {total_txns}\n"
        f"  ⏳ Pending:             {pending_txns}\n"
        f"  ✅ Approved:            {approved_txns}\n"
        f"  ❌ Rejected:            {rejected_txns}\n"
        f"💰 **Total Revenue:**     ₹{total_revenue:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 **Accounts:**          {avail_accounts + sold_accounts}\n"
        f"  ✅ Available:           {avail_accounts}\n"
        f"  💰 Sold:               {sold_accounts}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 **Orders:**            {total_orders}\n"
        f"  🟢 Active:             {active_orders}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 **Updated:** {_now_ist()}"
    )


# ─────────────────────────────────────────────
#  ADD BALANCE
# ─────────────────────────────────────────────

async def add_bal(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.reply(
            "❌ **Usage:** `/addbal <user_id> <amount>`\n"
            "Example: `/addbal 123456789 500`"
        )
        return

    try:
        target_uid = int(parts[1])
        amount     = float(parts[2])
        if amount <= 0:
            raise ValueError("Non-positive amount")
    except ValueError:
        await message.reply("❌ **Invalid arguments.** user_id must be integer, amount must be positive number.")
        return

    new_balance = add_balance(target_uid, amount)

    await message.reply(
        f"✅ **Balance Added!**\n\n"
        f"👤 User ID: `{target_uid}`\n"
        f"➕ Added: ₹{amount:.2f}\n"
        f"💰 New Balance: ₹{new_balance:.2f}"
    )

    # Notify the user
    try:
        await client.send_message(
            target_uid,
            f"🎁 **Wallet Top-Up!**\n\n"
            f"An admin has added ₹{amount:.2f} to your wallet.\n"
            f"💰 **New Balance:** ₹{new_balance:.2f}\n\n"
            f"🛒 Head to the Shop to buy an account!"
        )
    except Exception as e:
        logger.warning(f"⚠️ Could not notify user {target_uid}: {e}")
        await message.reply(f"⚠️ Balance added but couldn't notify user (they may not have started the bot).")


# ─────────────────────────────────────────────
#  BROADCAST
# ─────────────────────────────────────────────

async def broadcast(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    text_parts = message.text.split(None, 1)
    if len(text_parts) < 2 or not text_parts[1].strip():
        await message.reply(
            "❌ **Usage:** `/broadcast <message>`\n"
            "Example: `/broadcast 🎉 New accounts added!`"
        )
        return

    broadcast_text = text_parts[1].strip()
    user_ids       = get_all_user_ids()
    total          = len(user_ids)

    status_msg = await message.reply(
        f"📢 **Broadcasting...**\n\n"
        f"👥 Sending to {total} users..."
    )

    sent    = 0
    failed  = 0

    for uid in user_ids:
        try:
            await client.send_message(uid, f"📢 **Announcement**\n\n{broadcast_text}")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Rate limit: ~20 msgs/sec

    try:
        await status_msg.edit_text(
            f"✅ **Broadcast Complete!**\n\n"
            f"👥 Total users: {total}\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}"
        )
    except Exception:
        await message.reply(
            f"✅ **Broadcast Complete!**\n\n"
            f"✅ Sent: {sent} | ❌ Failed: {failed}"
        )


# ─────────────────────────────────────────────
#  MANAGE ADMINS
# ─────────────────────────────────────────────

async def manage_admins(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    cmd   = parts[0].lstrip("/").lower()

    if len(parts) != 2:
        await message.reply(f"❌ **Usage:** `/{cmd} <user_id>`")
        return

    try:
        target_uid = int(parts[1])
    except ValueError:
        await message.reply("❌ User ID must be a number.")
        return

    if cmd == "addadmin":
        add_admin(target_uid)
        await message.reply(f"✅ User `{target_uid}` has been promoted to **Admin**! 👑")
        try:
            await client.send_message(
                target_uid,
                "🎉 **You've been promoted to Admin!**\n\n"
                "You now have access to all admin commands.\n"
                "Use /help to see admin commands."
            )
        except Exception:
            pass

    elif cmd == "rmadmin":
        success = remove_admin(target_uid)
        if not success:
            await message.reply("❌ **Cannot remove the primary admin!**")
        else:
            await message.reply(f"✅ User `{target_uid}` has been **removed from admins**.")
            try:
                await client.send_message(
                    target_uid,
                    "⚠️ **Admin Access Revoked**\n\n"
                    "Your admin privileges have been removed."
                )
            except Exception:
                pass


# ─────────────────────────────────────────────
#  SET UPI
# ─────────────────────────────────────────────

async def set_config_cmd(client, message: Message):
    """Handles /setupi, /setfsub, /recovery, /fa2"""
    if not is_admin(message.from_user.id):
        return

    admin_id = message.from_user.id
    parts    = message.text.split(None, 2)
    cmd      = parts[0].lstrip("/").lower()

    # ── /setupi <upi_id> <name> ──────────────────
    if cmd == "setupi":
        if len(parts) < 3:
            await message.reply(
                "❌ **Usage:** `/setupi <upi_id> <display_name>`\n"
                "Example: `/setupi yourname@upi OTP Ocean`"
            )
            return

        upi_id   = parts[1]
        upi_name = parts[2]
        update_config("upi_id",   upi_id)
        update_config("upi_name", upi_name)

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ Upload QR Code Image", callback_data="set_upi_image")]
        ])
        await message.reply(
            f"✅ **UPI Details Updated!**\n\n"
            f"💳 **UPI ID:** `{upi_id}`\n"
            f"👤 **Name:** {upi_name}\n\n"
            f"_(Optional) Upload a QR code image:_",
            reply_markup=markup
        )

    # ── /setfsub ─────────────────────────────────
    elif cmd == "setfsub":
        await show_fsub_manager(client, message)

    # ── /recovery <email> ────────────────────────
    elif cmd == "recovery":
        if len(parts) < 2:
            await message.reply("❌ **Usage:** `/recovery <email>`")
            return

        email = parts[1]
        if "@" not in email or "." not in email:
            await message.reply("❌ **Invalid email format!** Must contain @ and .")
            return

        update_config("recovery_email", email)
        await message.reply(
            f"✅ **Default Recovery Email Set:**\n`{email}`\n\n"
            f"This will be used for new accounts via /login."
        )

    # ── /fa2 <password> ──────────────────────────
    elif cmd == "fa2":
        if len(parts) < 2:
            await message.reply("❌ **Usage:** `/fa2 <password>`")
            return

        pwd = parts[1]
        if len(pwd) < 6:
            await message.reply("❌ 2FA password must be at least 6 characters.")
            return

        update_config("admin_2fa", pwd)
        await message.reply(
            f"✅ **Default 2FA Password Set!**\n\n"
            f"This will be used for new accounts via /login."
        )


# ─────────────────────────────────────────────
#  FSUB MANAGER
# ─────────────────────────────────────────────

async def show_fsub_manager(client, update):
    """Show current FSub channels with add/remove buttons."""
    from pyrogram.types import CallbackQuery

    if isinstance(update, CallbackQuery):
        chat_id = update.from_user.id
        old_msg = update.message
    else:
        chat_id = update.from_user.id
        old_msg = None

    channels = get_fsub_channels()
    ch_text  = "\n".join([f"  • `{ch}`" for ch in channels]) if channels else "  _(none configured)_"

    text = (
        "📢 **Force Subscribe Manager**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Current Channels:**\n{ch_text}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [[InlineKeyboardButton("➕ Add Channel", callback_data="add_fsub_prompt")]]
    if channels:
        buttons.append([InlineKeyboardButton("➖ Remove Channel", callback_data="rm_fsub_menu")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])

    markup = InlineKeyboardMarkup(buttons)

    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            pass
        await client.send_message(chat_id, text, reply_markup=markup)
    else:
        await update.reply(text, reply_markup=markup)


async def show_rm_fsub_menu(client, callback: CallbackQuery):
    """Show buttons to remove individual FSub channels."""
    channels = get_fsub_channels()
    chat_id  = callback.from_user.id

    if not channels:
        await callback.answer("ℹ️ No channels to remove.", show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(f"🗑️ {ch}", callback_data=f"rm_fsub_{ch}")]
        for ch in channels
    ]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="setfsub_menu")])

    try:
        await callback.message.delete()
    except Exception:
        pass

    await client.send_message(
        chat_id,
        "➖ **Select a channel to remove:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ─────────────────────────────────────────────
#  SOLD ACCOUNTS LOG
# ─────────────────────────────────────────────

async def sold_accounts(client, message: Message):
    if not is_admin(message.from_user.id):
        return

    orders = list(
        _col("orders").find({}).sort("timestamp", -1).limit(50)
    )

    if not orders:
        await message.reply("📋 **No orders yet.**")
        return

    lines = ["📋 **Recent 50 Orders**\n━━━━━━━━━━━━━━━━━━━━━━━━"]
    for o in orders:
        ts   = o.get("timestamp")
        date = ts.strftime("%d/%m %H:%M") if ts else "N/A"
        status_icon = "🟢" if o.get("status") == "active" else "🔒"
        lines.append(
            f"{status_icon} `{o['phone']}` | 👤 `{o['user_id']}` | "
            f"💰 ₹{o.get('price', 0):.0f} | 📅 {date}"
        )

    # Telegram message limit — chunk if needed
    full_text = "\n".join(lines)
    if len(full_text) > 4096:
        chunks = [lines[0]]
        for line in lines[1:]:
            if len("\n".join(chunks + [line])) > 4000:
                await message.reply("\n".join(chunks))
                chunks = [line]
            else:
                chunks.append(line)
        if chunks:
            await message.reply("\n".join(chunks))
    else:
        await message.reply(full_text)


# ─────────────────────────────────────────────
#  INTERACTIVE /addacc FLOW
# ─────────────────────────────────────────────

async def add_acc_start(client, message: Message):
    """Start the interactive add-account flow."""
    if not is_admin(message.from_user.id):
        return

    admin_id = message.from_user.id
    admin_states[admin_id] = {"step": "phone"}

    await message.reply(
        "➕ **Add Account — Step 1/4**\n\n"
        "📱 Enter the phone number (with country code):\n"
        "Example: `+919876543210`\n\n"
        "_Send /cancellogin to abort._"
    )


# ─────────────────────────────────────────────
#  UPI IMAGE UPLOAD
# ─────────────────────────────────────────────

async def set_upi_image_start(client, callback: CallbackQuery):
    """Trigger state to receive a UPI QR image from admin."""
    admin_id = callback.from_user.id
    if not is_admin(admin_id):
        await callback.answer("❌ Not an admin!", show_alert=True)
        return

    admin_states[admin_id] = {"step": "waiting_upi_image"}
    await callback.answer("📸 Send the QR code image now.", show_alert=False)
    await client.send_message(
        admin_id,
        "📸 **Send the UPI QR code image now.**\n\n"
        "_(Send as a photo, not as a file)_"
    )


# ─────────────────────────────────────────────
#  ADMIN MESSAGE STATE MACHINE
# ─────────────────────────────────────────────

async def handle_admin_msg(client, message: Message):
    """
    Processes admin-state-driven messages:
    - waiting_upi_image
    - waiting_fsub_channel
    - phone → session → country → price  (/addacc flow)
    """
    admin_id = message.from_user.id
    state    = admin_states.get(admin_id)

    if not state:
        return

    step = state.get("step")

    # ── Waiting for UPI QR image ─────────────────
    if step == "waiting_upi_image":
        if not message.photo:
            await message.reply("📸 Please send a **photo**, not a file.")
            return
        file_id = message.photo.file_id
        update_config("upi_image_file_id", file_id)
        admin_states.pop(admin_id, None)
        await message.reply("✅ **UPI QR code image saved successfully!**")

    # ── Waiting for FSub channel ─────────────────
    elif step == "waiting_fsub_channel":
        ch = (message.text or "").strip()
        if not ch:
            await message.reply("⚠️ Please enter a channel ID or @username.")
            return

        add_fsub_channel(ch)
        admin_states.pop(admin_id, None)
        await message.reply(
            f"✅ **Channel added to FSub list:**\n`{ch}`\n\n"
            f"Users must now join this channel to use the bot."
        )

    # ── /addacc: phone ───────────────────────────
    elif step == "phone":
        phone = (message.text or "").strip()
        if not phone.startswith("+") or len(phone) < 7:
            await message.reply("❌ Invalid phone. Must start with + and include country code.\nExample: `+919876543210`")
            return
        state.update({"step": "session", "phone": phone})
        await message.reply(
            f"✅ Phone: `{phone}`\n\n"
            f"**Step 2/4** — Enter the **Pyrogram session string** for this account:"
        )

    # ── /addacc: session ─────────────────────────
    elif step == "session":
        session = (message.text or "").strip()
        if len(session) < 50:
            await message.reply("❌ Session string seems too short. Please check and try again.")
            return
        state.update({"step": "country", "session": session})
        await message.reply(
            "✅ Session string saved.\n\n"
            "**Step 3/4** — Enter the **country name**:\n"
            "Example: `INDIA` or `USA`"
        )

    # ── /addacc: country ─────────────────────────
    elif step == "country":
        country = (message.text or "").strip().upper()
        if not country:
            await message.reply("❌ Country cannot be empty.")
            return
        state.update({"step": "price", "country": country})
        await message.reply(
            f"✅ Country: `{country}`\n\n"
            f"**Step 4/4** — Enter the **price** (in ₹):\n"
            f"Example: `199` or `299.50`"
        )

    # ── /addacc: price ───────────────────────────
    elif step == "price":
        try:
            price = float((message.text or "").strip())
            if price <= 0:
                raise ValueError("Non-positive")
        except ValueError:
            await message.reply("❌ Price must be a positive number. Example: `199`")
            return

        phone   = state["phone"]
        session = state["session"]
        country = state["country"]

        cfg             = get_config()
        default_pwd     = cfg.get("admin_2fa") or ""
        default_email   = cfg.get("recovery_email") or ""

        add_account(phone, session, country, price, default_pwd, default_email)
        admin_states.pop(admin_id, None)

        await message.reply(
            f"✅ **Account Added to Shop!**\n\n"
            f"📱 **Phone:** `{phone}`\n"
            f"🌍 **Country:** {country}\n"
            f"💰 **Price:** ₹{price:.2f}\n"
            f"🔐 **2FA:** `{default_pwd or 'not set'}`\n"
            f"📧 **Email:** `{default_email or 'not set'}`\n\n"
            f"🛒 Account is now **available** in the shop!"
        )
