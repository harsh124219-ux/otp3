"""
handlers/fsub.py — Force-subscribe channel enforcement.
Checks if a user has joined all required channels before accessing the bot.
"""

import logging
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelInvalid

logger = logging.getLogger(__name__)


async def check_fsub(client, update) -> bool:
    """
    Check if the user has joined all required FSub channels.
    update can be a Message or CallbackQuery.

    Returns True  → user passes, allow access.
    Returns False → user blocked, join buttons already sent.
    """
    from database import get_fsub_channels, is_admin
    from pyrogram.types import CallbackQuery

    # Extract user info correctly from Message or CallbackQuery
    if isinstance(update, CallbackQuery):
        user_id    = update.from_user.id
        first_name = update.from_user.first_name
        send_fn    = update.message.reply   # reply to the message inside the callback
        async def send_msg(text, **kwargs):
            try:
                await update.message.delete()
            except Exception:
                pass
            return await client.send_message(user_id, text, **kwargs)
    else:
        user_id    = update.from_user.id
        first_name = update.from_user.first_name
        async def send_msg(text, **kwargs):
            return await update.reply(text, **kwargs)

    # Admins bypass FSub always
    if is_admin(user_id):
        return True

    channels = get_fsub_channels()
    if not channels:
        return True  # No restriction configured

    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    failed_channels = []

    for ch in channels:
        try:
            member = await client.get_chat_member(ch, user_id)
            status = member.status.value if hasattr(member.status, "value") else str(member.status)
            # banned or left = not subscribed
            if status in ("banned", "left", "kicked"):
                failed_channels.append(ch)
        except UserNotParticipant:
            failed_channels.append(ch)
        except (ChatAdminRequired, ChannelInvalid) as e:
            logger.warning(f"⚠️ FSub check error for channel {ch}: {e}")
            # Non-blocking — skip this channel if bot has no access
        except Exception as e:
            logger.warning(f"⚠️ Unexpected FSub error for {ch}: {e}")

    if not failed_channels:
        return True

    # Build join buttons for all failed channels
    buttons = []
    for ch in failed_channels:
        try:
            chat = await client.get_chat(ch)
            title = chat.title or ch
            # Try to get invite link
            try:
                invite = await client.export_chat_invite_link(ch)
            except Exception:
                invite = f"https://t.me/{ch.lstrip('@')}" if ch.startswith("@") else None

            if invite:
                buttons.append([InlineKeyboardButton(f"📢 Join {title}", url=invite)])
        except Exception as e:
            logger.warning(f"⚠️ Could not get chat info for {ch}: {e}")
            buttons.append([InlineKeyboardButton(f"📢 Join Channel", url=f"https://t.me/{ch.lstrip('@')}")])

    buttons.append([InlineKeyboardButton("✅ I've Joined — Check Again", callback_data="check_fsub_again")])

    text = (
        "🔒 **Access Denied!**\n\n"
        "You must join our official channel(s) to use **OTP Ocean**.\n"
        "After joining, tap the button below to verify.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Hi **{first_name}**, join the channel(s) below:"
    )

    await send_msg(text, reply_markup=InlineKeyboardMarkup(buttons))
    return False


async def recheck_fsub_callback(client, callback) -> None:
    """
    Called when user taps '✅ I've Joined — Check Again'.
    Re-runs the FSub check and either welcomes them or shows remaining channels.
    """
    from database import get_fsub_channels
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from pyrogram.errors import UserNotParticipant

    user_id    = callback.from_user.id
    first_name = callback.from_user.first_name

    channels = get_fsub_channels()
    if not channels:
        await callback.answer("✅ Welcome!", show_alert=False)
        try:
            await callback.message.delete()
        except Exception:
            pass
        from handlers.user import start
        await start(client, callback)
        return

    failed_channels = []
    for ch in channels:
        try:
            member = await client.get_chat_member(ch, user_id)
            status = member.status.value if hasattr(member.status, "value") else str(member.status)
            if status in ("banned", "left", "kicked"):
                failed_channels.append(ch)
        except UserNotParticipant:
            failed_channels.append(ch)
        except Exception as e:
            logger.warning(f"⚠️ Recheck FSub error for {ch}: {e}")

    if not failed_channels:
        await callback.answer("✅ Verified! Welcome to OTP Ocean 🌊", show_alert=False)
        try:
            await callback.message.delete()
        except Exception:
            pass
        from handlers.user import start
        await start(client, callback)
        return

    # Still not joined — rebuild buttons for remaining channels
    buttons = []
    for ch in failed_channels:
        try:
            chat = await client.get_chat(ch)
            title = chat.title or ch
            try:
                invite = await client.export_chat_invite_link(ch)
            except Exception:
                invite = f"https://t.me/{ch.lstrip('@')}" if ch.startswith("@") else None
            if invite:
                buttons.append([InlineKeyboardButton(f"📢 Join {title}", url=invite)])
        except Exception:
            buttons.append([InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{ch.lstrip('@')}")])

    buttons.append([InlineKeyboardButton("✅ I've Joined — Check Again", callback_data="check_fsub_again")])

    await callback.answer("❌ You haven't joined all channels yet!", show_alert=True)
    try:
        await callback.message.edit_text(
            f"🔒 **Still Missing!**\n\n"
            f"Hey **{first_name}**, you still need to join the channel(s) below:\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"After joining all, tap ✅ Check Again.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        pass
