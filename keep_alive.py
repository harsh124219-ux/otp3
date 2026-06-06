"""
keep_alive.py — Self-pinger to prevent Render/Railway free-tier sleep.

How it works:
- Every 14 minutes, this pings your own app's URL
- Render free tier sleeps after 15 minutes of no traffic
- Railway free tier has execution limits — ping keeps the service warm
- If APP_URL is not set, this module does nothing (safe for local/VPS use)
"""

import asyncio
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

# Ping every 14 minutes (Render sleeps at 15 min, Railway at ~15 min)
PING_INTERVAL_SECONDS = 14 * 60  # 840 seconds


async def self_ping_loop() -> None:
    """
    Continuously pings APP_URL every 14 minutes to prevent sleep.
    Runs as a background asyncio task — never blocks the bot.
    Safe no-op if APP_URL is not configured.
    """
    app_url = os.environ.get("APP_URL", "").strip().rstrip("/")

    if not app_url:
        logger.info("ℹ️  APP_URL not set — keep-alive self-ping disabled.")
        return

    ping_url = f"{app_url}/health"
    logger.info(f"💓 Keep-alive self-ping enabled → {ping_url} (every {PING_INTERVAL_SECONDS // 60} min)")

    # Wait 30 seconds after startup before first ping
    await asyncio.sleep(30)

    consecutive_failures = 0

    while True:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(ping_url) as resp:
                    if resp.status == 200:
                        consecutive_failures = 0
                        logger.debug(f"💓 Self-ping OK ({resp.status})")
                    else:
                        consecutive_failures += 1
                        logger.warning(
                            f"⚠️  Self-ping returned {resp.status} "
                            f"(failure #{consecutive_failures})"
                        )

        except asyncio.TimeoutError:
            consecutive_failures += 1
            logger.warning(f"⚠️  Self-ping timed out (failure #{consecutive_failures})")

        except aiohttp.ClientConnectorError as e:
            consecutive_failures += 1
            logger.warning(f"⚠️  Self-ping connection error: {e} (failure #{consecutive_failures})")

        except Exception as e:
            consecutive_failures += 1
            logger.warning(f"⚠️  Self-ping error: {e} (failure #{consecutive_failures})")

        # Log a stronger warning after 3 consecutive failures
        if consecutive_failures == 3:
            logger.error(
                "❌ 3 consecutive self-ping failures! "
                "Check your APP_URL environment variable."
            )

        await asyncio.sleep(PING_INTERVAL_SECONDS)


def start_keep_alive() -> asyncio.Task:
    """
    Create and return the background keep-alive task.
    Call this inside an async context after the bot starts.

    Usage in main.py:
        from keep_alive import start_keep_alive
        keep_alive_task = start_keep_alive()
    """
    loop = asyncio.get_event_loop()
    task = loop.create_task(self_ping_loop())
    return task
