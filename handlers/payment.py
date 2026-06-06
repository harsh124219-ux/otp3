"""
handlers/payment.py — Admin payment approval and rejection flow.
Approve: instantly adds balance and notifies user.
Reject: asks admin for a reason, then forwards reason + screenshot back to user.
"""

import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from database import (
    get_transaction,
    update_transaction_status,
    add_balance,
    get_balance,
    is_admin,
)
from info import LOG_GROUP

logger = logging.getLogger(__name__)

# ── Admin rejection state: {admin_id: {"utr": str, "user_id": int, "log_message": Message}} ──
payment_admin_states: dict = {}


# ─────────────────────────────────────────────
#  PAYMENT CALLBACK (approve / reject buttons)
# ─────────────────────────────────────────────

async def payment_callback(client, callback: CallbackQuery):
    """
    Handles approve_{utr}_{user_id}_{amount} and reject_{utr}_{user_id} callbacks
    that appear on payment screenshots in the LOG_GROUP.
    """
    admin_id = callback.from_user.id

    if not is_admin(admin_id):
        await callback.answer("❌ You are not an admin!", show_alert=True)
        return

    data = callback.data

    # ──────────────────────────────────────────
    #  APPROVE
    # ──────────────────────────────────────────
    if data.startswith("approve_"):
        parts = data.split("_")
        # approve_{utr}_{user_id}_{amount}
        # utr may contain underscores? No — UTR is digits only, so safe to split by "_"
        utr     = parts[1]
        user_id = int(parts[2])
        amount  = float(parts[3])

        txn = get_transaction(utr)
        if not txn:
            await callback.answer("❌ Transaction not found!", show_alert=True)
            return

        if txn["status"] == "approved":
            await callback.answer("⚠️ Already approved!", show_alert=True)
            return

        if txn["status"] == "rejected":
            await callback.answer("⚠️ This was already rejected!", show_alert=True)
            return

        # Add balance and mark approved
        new_balance = add_balance(user_id, amount)
        update_transaction_status(utr, "approved")

        # Edit the log message to reflect approval
        try:
            original_caption = callback.message.caption or ""
            await callback.message.edit_caption(
                original_caption +
                f"\n\n✅ **APPROVED** by {callback.from_user.first_name} (`{admin_id}`)"
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not edit log caption: {e}")

        # Notify the user
        try:
            await client.send_message(
                user_id,
                f"✅ **Payment Approved!**\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 **Added:** ₹{amount:.2f}\n"
                f"🔢 **UTR:** `{utr}`\n"
                f"💳 **New Balance:** ₹{new_balance:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🛒 Head to the Shop to buy an account!"
            )
        except Exception as e:
            logger.error(f"❌ Could not notify user {user_id} of approval: {e}")

        await callback.answer("✅ Payment approved and balance added!", show_alert=False)

    # ──────────────────────────────────────────
    #  REJECT (step 1 — ask for reason)
    # ──────────────────────────────────────────
    elif data.startswith("reject_"):
        parts   = data.split("_")
        utr     = parts[1]
        user_id = int(parts[2])

        txn = get_transaction(utr)
        if not txn:
            await callback.answer("❌ Transaction not found!", show_alert=True)
            return

        if txn["status"] == "rejected":
            await callback.answer("⚠️ Already rejected!", show_alert=True)
            return

        if txn["status"] == "approved":
            await callback.answer("⚠️ Already approved — cannot reject!", show_alert=True)
            return

        # Save state so the next text message from this admin becomes the reason
        payment_admin_states[admin_id] = {
            "utr":         utr,
            "user_id":     user_id,
            "log_message": callback.message,
        }

        await callback.answer("📝 Please type the rejection reason.", show_alert=False)

        # Ask admin for reason in their private chat
        try:
            await client.send_message(
                admin_id,
                f"💬 **Enter rejection reason for UTR** `{utr}`:\n\n"
                f"_(Your next message will be sent as the reason to the user)_"
            )
        except Exception as e:
            logger.error(f"❌ Could not message admin {admin_id}: {e}")
            payment_admin_states.pop(admin_id, None)


# ─────────────────────────────────────────────
#  REJECTION REASON HANDLER (step 2)
# ─────────────────────────────────────────────

async def handle_admin_rejection_reason(client, message):
    """
    Called from main.py msg_h when admin_id is in payment_admin_states.
    The admin's text is the rejection reason.
    """
    admin_id = message.from_user.id
    state    = payment_admin_states.get(admin_id)

    if not state:
        return

    reason      = (message.text or "").strip()
    utr         = state["utr"]
    user_id     = state["user_id"]
    log_message = state["log_message"]

    if not reason:
        await message.reply("⚠️ Reason cannot be empty. Please type a reason:")
        return

    # Mark as rejected in DB
    update_transaction_status(utr, "rejected")

    # Edit the log group message
    try:
        original_caption = log_message.caption or ""
        await log_message.edit_caption(
            original_caption +
            f"\n\n❌ **REJECTED** by {message.from_user.first_name} (`{admin_id}`)\n"
            f"📝 **Reason:** {reason}"
        )
    except Exception as e:
        logger.warning(f"⚠️ Could not edit log caption: {e}")

    # Fetch the screenshot and re-send to user with reason
    txn = get_transaction(utr)
    if txn and txn.get("ss_file_id"):
        try:
            await client.send_photo(
                user_id,
                txn["ss_file_id"],
                caption=(
                    f"❌ **Payment Rejected**\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔢 **UTR:** `{utr}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"⚠️ **Reason:** {reason}\n\n"
                    f"Please review your payment and resubmit with correct proof.\n"
                    f"Contact @OTPOceanSupportBot if you need help."
                )
            )
        except Exception as e:
            logger.error(f"❌ Could not send rejection to user {user_id}: {e}")
            # Fallback: send text-only notification
            try:
                await client.send_message(
                    user_id,
                    f"❌ **Payment Rejected**\n\n"
                    f"🔢 **UTR:** `{utr}`\n"
                    f"⚠️ **Reason:** {reason}\n\n"
                    f"Please review and resubmit. Contact @OTPOceanSupportBot for help."
                )
            except Exception as e2:
                logger.error(f"❌ Could not send text rejection to user {user_id}: {e2}")
    else:
        try:
            await client.send_message(
                user_id,
                f"❌ **Payment Rejected**\n\n"
                f"🔢 **UTR:** `{utr}`\n"
                f"⚠️ **Reason:** {reason}\n\n"
                f"Please resubmit with correct proof."
            )
        except Exception as e:
            logger.error(f"❌ Could not notify user {user_id} of rejection: {e}")

    # Confirm to admin
    await message.reply(
        f"✅ **Rejection processed!**\n\n"
        f"🔢 UTR: `{utr}`\n"
        f"📝 Reason sent to user `{user_id}`."
    )

    payment_admin_states.pop(admin_id, None)
