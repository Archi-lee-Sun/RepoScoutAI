import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NODES_DIR = os.path.dirname(__file__)
for path in (ROOT_DIR, NODES_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import CallbackQuery

try:
    from nodes.state import PendingRepos, PreferenceMemory, RepoStatus
    from nodes.github_client import star_repo
except ModuleNotFoundError:
    from state import PendingRepos, PreferenceMemory, RepoStatus
    from github_client import star_repo

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


@dp.callback_query(F.data.startswith("a:") | F.data.startswith("r:"))
async def handle_decision(callback: CallbackQuery) -> None:
    action, separator, repo_id = callback.data.partition(":")
    if not separator or action not in {"a", "r"} or not repo_id:
        await callback.answer("Invalid action", show_alert=True)
        return

    pending_store = PendingRepos()
    entry = pending_store.claim(repo_id)
    if entry is None:
        logger.info("Callback %s is already handled or being processed", repo_id)
        await callback.answer("Already handled or processing")
        return

    full_name = entry["full_name"]
    url = entry["url"]
    description = entry["description"]
    selector_reason = entry.get("selector_reason")
    history = PreferenceMemory()

    if action == "a":
        starred = await asyncio.to_thread(star_repo, full_name)
        if not starred:
            pending_store.release(repo_id)
            await callback.answer("GitHub star failed; press again to retry", show_alert=True)
            return

        history.add_decision(
            full_name, url, "accept", description, selector_reason, decision_id=repo_id
        )
        RepoStatus().add_entry(
            full_name=full_name,
            starred_at=datetime.now(timezone.utc).isoformat(),
            selector_reason=selector_reason,
        )
        result_text = f"✅ Starred: {full_name}"
    else:
        history.add_decision(
            full_name, url, "reject", description, selector_reason, decision_id=repo_id
        )
        result_text = f"❌ Rejected: {full_name}"

    pending_store.remove(repo_id)
    try:
        await callback.message.edit_text(result_text)
    except Exception:
        logger.exception("[bot] failed to edit message for %s", full_name)
    await callback.answer()
    logger.info("[bot] processed %s for %s", action, full_name)


async def main() -> None:
    logger.info("[bot] starting long polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
