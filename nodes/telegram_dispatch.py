import asyncio
import logging
import os
import secrets
import sys
from dataclasses import dataclass, field

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NODES_DIR = os.path.dirname(__file__)
for path in (ROOT_DIR, NODES_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from state import Candidate, PendingRepos

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    sent: set[str] = field(default_factory=set)
    failed: dict[str, Exception] = field(default_factory=dict)


async def dispatch_batch(candidates: list[Candidate]) -> DispatchResult:
    result = DispatchResult()
    if not candidates:
        return result
    if not TELEGRAM_CHAT_ID or not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    pending_store = PendingRepos()
    pruned_count = pending_store.prune_old_entries()
    if pruned_count:
        logger.info("removed %s expired callback entries", pruned_count)

    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        for candidate in candidates:
            full_name = candidate.full_name
            explanation_ka = candidate.explanation_ka
            callback_id = None
            pending_added = False

            if not explanation_ka:
                error = RuntimeError("Georgian explanation is missing")
                result.failed[full_name] = error
                logger.error("[dispatch] missing Georgian explanation for %s", full_name)
                continue

            callback_id = secrets.token_hex(4)
            pending_store.add(callback_id, {
                "full_name": full_name,
                "url": candidate.url,
                "description": candidate.description,
                "selector_reason": candidate.selector_reason,
            })
            pending_added = True

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Accept", callback_data=f"a:{callback_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"r:{callback_id}"),
            ]])
            text = (
                f"📦 {full_name} (⭐ {candidate.stars})\n"
                f"🔗 {candidate.url}\n\n{explanation_ka}"
            )
            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception as exc:
                if pending_added and callback_id:
                    try:
                        pending_store.remove(callback_id)
                    except Exception:
                        logger.exception(
                            "[dispatch] failed to remove orphan pending ID %s", callback_id
                        )
                if isinstance(exc, (TelegramUnauthorizedError, TelegramForbiddenError)):
                    raise
                result.failed[full_name] = exc
                logger.warning(
                    "[dispatch] candidate remains retryable: %s (%s: %s)",
                    full_name,
                    type(exc).__name__,
                    str(exc)[:240],
                )
                continue

            result.sent.add(full_name)
            logger.info("successfully sent: %s (ID: %s)", full_name, callback_id)
            await asyncio.sleep(0.5)
    finally:
        await bot.session.close()

    return result
