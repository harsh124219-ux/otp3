"""
database.py — All MongoDB operations for OTP Ocean.
Uses _col() getter pattern so collections are never stale after reconnect.
init_db() must be called once in main() before the bot starts.
"""

import logging
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

# Module-level references — set by init_db()
_mongo_client = None
db = None


def init_db():
    """Connect to MongoDB. Call once in main() before bot starts."""
    global _mongo_client, db
    from info import MONGO_URL, ADMIN_ID
    _mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = _mongo_client["otpbot"]
    # Force a connection check
    _mongo_client.admin.command("ping")
    logger.info("✅ MongoDB connected successfully.")

    # Create indexes for performance
    try:
        db["users"].create_index("user_id", unique=True)
        db["transactions"].create_index("utr", unique=True)
        db["accounts"].create_index("phone", unique=True)
        db["orders"].create_index("order_id", unique=True)
        db["orders"].create_index("user_id")
        db["accounts"].create_index([("status", ASCENDING), ("country", ASCENDING)])
        logger.info("✅ DB indexes ensured.")
    except Exception as e:
        logger.warning(f"⚠️ Index creation warning (non-fatal): {e}")


def _col(name: str):
    """Always fetch collection from the live db object — never stale."""
    return db[name] if db is not None else None


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

def get_config() -> dict:
    """Return the settings document. Creates default if missing."""
    from info import ADMIN_ID
    col = _col("config")
    doc = col.find_one({"type": "settings"})
    if doc is None:
        default = {
            "type": "settings",
            "admins": [ADMIN_ID],
            "fsub_channels": [],
            "upi_id": None,
            "upi_name": None,
            "upi_image_file_id": None,
            "recovery_email": None,
            "admin_2fa": None,
            "updated_at": datetime.now(timezone.utc),
        }
        col.insert_one(default)
        doc = col.find_one({"type": "settings"})
    return doc


def update_config(key: str, value) -> None:
    """Update a single field in the settings document."""
    _col("config").update_one(
        {"type": "settings"},
        {"$set": {key: value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def is_admin(user_id: int) -> bool:
    """True if user_id is in the admins list."""
    cfg = get_config()
    return user_id in cfg.get("admins", [])


def add_admin(user_id: int) -> None:
    """Add user_id to admins (no-op if already present)."""
    _col("config").update_one(
        {"type": "settings"},
        {"$addToSet": {"admins": user_id}},
        upsert=True,
    )


def remove_admin(user_id: int) -> bool:
    """
    Remove user_id from admins.
    Returns False (without removing) if user_id is the primary ADMIN_ID.
    Returns True on successful removal.
    """
    from info import ADMIN_ID
    if user_id == ADMIN_ID:
        return False
    _col("config").update_one(
        {"type": "settings"},
        {"$pull": {"admins": user_id}},
    )
    return True


def get_fsub_channels() -> list:
    """Return list of FSub channel IDs/usernames. Empty list if none configured."""
    cfg = get_config()
    return cfg.get("fsub_channels", [])


def add_fsub_channel(channel: str) -> None:
    """Add a channel to FSub list (no duplicates)."""
    _col("config").update_one(
        {"type": "settings"},
        {"$addToSet": {"fsub_channels": channel}},
        upsert=True,
    )


def remove_fsub_channel(channel: str) -> None:
    """Remove a channel from FSub list."""
    _col("config").update_one(
        {"type": "settings"},
        {"$pull": {"fsub_channels": channel}},
    )


# ─────────────────────────────────────────────
#  USERS
# ─────────────────────────────────────────────

def get_user(user_id: int, username: str = None, first_name: str = None) -> dict:
    """
    Fetch user doc. Creates default if missing.
    Also updates username and first_name if provided.
    """
    col = _col("users")
    doc = col.find_one({"user_id": user_id})
    if doc is None:
        doc = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name or "User",
            "balance": 0.0,
            "total_spent": 0.0,
            "joined": datetime.now(timezone.utc),
        }
        col.insert_one(doc)
        doc = col.find_one({"user_id": user_id})
    else:
        # Update name/username if provided
        updates = {}
        if username is not None:
            updates["username"] = username
        if first_name is not None:
            updates["first_name"] = first_name
        if updates:
            col.update_one({"user_id": user_id}, {"$set": updates})
            doc.update(updates)
    return doc


def get_balance(user_id: int) -> float:
    """Return wallet balance for a user."""
    doc = get_user(user_id)
    return doc.get("balance", 0.0)


def add_balance(user_id: int, amount: float) -> float:
    """
    Add amount to user balance.
    Returns new balance after update.
    """
    get_user(user_id)  # Ensure user exists
    _col("users").update_one(
        {"user_id": user_id},
        {"$inc": {"balance": amount}},
    )
    return get_balance(user_id)


def deduct_balance(user_id: int, amount: float) -> bool:
    """
    Deduct amount from balance if sufficient.
    Also increments total_spent.
    Returns True on success, False if insufficient balance.
    """
    doc = get_user(user_id)
    if doc.get("balance", 0.0) < amount:
        return False
    _col("users").update_one(
        {"user_id": user_id},
        {"$inc": {"balance": -amount, "total_spent": amount}},
    )
    return True


def get_all_users() -> list:
    """Return list of all user documents."""
    return list(_col("users").find({}))


def get_all_user_ids() -> list:
    """Return list of all user_id integers."""
    return [doc["user_id"] for doc in _col("users").find({}, {"user_id": 1})]


# ─────────────────────────────────────────────
#  TRANSACTIONS
# ─────────────────────────────────────────────

def add_transaction(user_id: int, utr: str, amount: float, ss_file_id: str) -> None:
    """Insert a new pending payment transaction."""
    _col("transactions").insert_one({
        "user_id": user_id,
        "utr": utr,
        "amount": amount,
        "ss_file_id": ss_file_id,
        "status": "pending",
        "timestamp": datetime.now(timezone.utc),
    })


def get_transaction(utr: str) -> dict | None:
    """Fetch transaction by UTR."""
    return _col("transactions").find_one({"utr": utr})


def update_transaction_status(utr: str, status: str) -> None:
    """Update status field: 'pending' | 'approved' | 'rejected'."""
    _col("transactions").update_one(
        {"utr": utr},
        {"$set": {"status": status}},
    )


def utr_exists(utr: str) -> bool:
    """True if this UTR has already been submitted (any status)."""
    return _col("transactions").count_documents({"utr": utr}) > 0


# ─────────────────────────────────────────────
#  ACCOUNTS
# ─────────────────────────────────────────────

def add_account(
    phone: str,
    session_string: str,
    country: str,
    price: float,
    password: str = "",
    recovery_email: str = "",
) -> None:
    """Upsert an account into the shop. Country is forced uppercase."""
    _col("accounts").update_one(
        {"phone": phone},
        {
            "$set": {
                "phone": phone,
                "session_string": session_string,
                "country": country.upper().strip(),
                "price": price,
                "status": "available",
                "password": password,
                "recovery_email": recovery_email,
                "added_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


def get_available_accounts(country: str = None) -> list:
    """Return available accounts, optionally filtered by country, sorted by price asc."""
    query = {"status": "available"}
    if country:
        query["country"] = country.upper().strip()
    return list(_col("accounts").find(query).sort("price", ASCENDING))


def get_accounts_by_country_sorted(country: str, sort_order: str) -> list:
    """
    Return available accounts for a country.
    sort_order: 'low_to_high' → price asc, 'high_to_low' → price desc.
    """
    direction = ASCENDING if sort_order == "low_to_high" else DESCENDING
    return list(
        _col("accounts").find(
            {"status": "available", "country": country.upper().strip()}
        ).sort("price", direction)
    )


def update_account_status(phone: str, status: str) -> None:
    """Set account status: 'available' | 'sold'."""
    _col("accounts").update_one(
        {"phone": phone},
        {"$set": {"status": status}},
    )


def clear_account_session(phone: str) -> None:
    """Clear the session_string from an account after it's been used/closed."""
    _col("accounts").update_one(
        {"phone": phone},
        {"$set": {"session_string": ""}},
    )


def get_all_countries() -> list:
    """Return distinct country names across ALL accounts (any status)."""
    return sorted(_col("accounts").distinct("country"))


def get_account(phone: str) -> dict | None:
    """Fetch a single account document by phone number."""
    return _col("accounts").find_one({"phone": phone})


# ─────────────────────────────────────────────
#  ORDERS
# ─────────────────────────────────────────────

def create_order(
    user_id: int,
    phone: str,
    session_string: str,
    country: str,
    price: float,
) -> str:
    """
    Create a new order. Copies session_string from account at purchase time.
    Returns the generated order_id string.
    """
    order_id = f"ORD{int(datetime.now(timezone.utc).timestamp())}"
    _col("orders").insert_one({
        "order_id": order_id,
        "user_id": user_id,
        "phone": phone,
        "session_string": session_string,
        "country": country,
        "price": price,
        "status": "active",
        "timestamp": datetime.now(timezone.utc),
    })
    return order_id


def get_user_orders(user_id: int) -> list:
    """Return all orders for a user, newest first."""
    return list(
        _col("orders").find({"user_id": user_id}).sort("timestamp", DESCENDING)
    )


def get_order(order_id: str) -> dict | None:
    """Fetch a single order by order_id."""
    return _col("orders").find_one({"order_id": order_id})


def close_order(order_id: str) -> None:
    """
    Close an order: set status='closed' and clear session_string
    from both the order and the original account document.
    """
    order = get_order(order_id)
    if order:
        _col("orders").update_one(
            {"order_id": order_id},
            {"$set": {"status": "closed", "session_string": ""}},
        )
        # Clear session from account doc too for security
        clear_account_session(order["phone"])
