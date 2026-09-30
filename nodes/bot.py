import asyncio
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import CallbackQuery

from state import PendingRepos, PreferenceMemory, RepoStatus
from github_client import star_repo

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


@dp.callback_query(
    F.data.startswith("a:") | F.data.startswith("r:")
)
async def handle_decision(callback: CallbackQuery) -> None:
    action , _ , repo_id = callable.data.partition(":")

    pending_store = PendingRepos()
    entry =  pending_store.get(repo_id)

    if entry is None :
        logger.info(f"Repo with pending ID {repo_id} was already handled or removed from pending.json")
        await callback.answer("Already handled")
        logger.info()

    full_name = entry["full_name"]
    url = entry["url"]
    description = entry["description"]
    selector_reason = entry.get("selector_reason")

    history = PreferenceMemory()

    if action == "a" :
        starred = star_repo(full_name)
        history.add_decision(full_name , url , "accept" , description , selector_reason)

        if starred :
            repo_status = RepoStatus()
            repo_status.add_entry(
                full_name=full_name,
                starred_at=datetime.now(timezone.utc).isoformat(),
                selector_reason=selector_reason,
            )
            result_text = f"✅ Starred: {full_name}"
        else :
            result_text = f"⚠️ Accepted, but starring failed: {full_name}"

    else :
        history.add_decision(full_name , url , "reject" , description , selector_reason)
        result_text = f"❌ Rejected: {full_name}"

    pending_store.remove(repo_id)


    try:
        await callback.message.edit_text(result_text)
    except Exception:
        logger.exception(f"[bot] failed to edit message for {full_name}")

    await callback.answer()
    logger.info(f"[bot] processed {action} for {full_name}")



async def main() -> None:
    logger.info("[bot] starting long polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())



