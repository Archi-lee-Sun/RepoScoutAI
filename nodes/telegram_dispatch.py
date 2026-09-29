import asyncio
import logging
import os
import secrets
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from state import PendingRepos , Candidate

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def dispatch_batch(candidates : list[Candidate]) :
    if not TELEGRAM_CHAT_ID or not TELEGRAM_TOKEN :
        logger.error("there is not telegram chat id or telegram token ")
        return

    pending_store = PendingRepos()
    pruned_count = pending_store.prune_old_entries()
    if pruned_count > 0 :
        logger.info(f"from pending.json there are removed {pruned_count} repos")

    bot = Bot(token=TELEGRAM_TOKEN)

    try :
        for candidate in candidates :
            full_name = candidate.full_name
            stars = candidate.stars
            url = candidate.url
            description = candidate.description
            explanation_ka = candidate.explanation_ka

            if not explanation_ka :
                logger.info(f"this repo called {full_name} has not explaination_ka")
                continue

            try :
                id = secrets.token_hex(4)
                pending_store.add(id , {
                    "full_name" : full_name ,
                    "url" : url , 
                    "description" : description ,
                    "selector_reason" : candidate.selector_reason ,
                })

                text = f"📦 {full_name} (⭐ {stars})\n🔗 {url}\n\n{explanation_ka}"

                keyboard = InlineKeyboardMarkup(
                    inline_keyboard = [
                        [
                            InlineKeyboardButton(
                                text="✅ Accept" ,
                                callback_data=f"a:{id}"
                            ) ,

                            InlineKeyboardButton(
                                text="❌ Reject" ,
                                callback_data=f"r:{id}"
                            )
                        ]
                    ]
                )

                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID ,
                    text=text ,
                    reply_markup=keyboard
                )

                logger.info(f"successfuly sent : {full_name} \n (ID : {id})")
                await asyncio.sleep(0.5)

            except Exception :
                    logger.exception(f"შეცდომა {full_name}-ის გაგზავნისას")
    finally :
        await bot.session.close()