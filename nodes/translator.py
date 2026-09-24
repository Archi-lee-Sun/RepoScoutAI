import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging

from dotenv import load_dotenv
from pydantic import BaseModel
from google.api_core.exceptions import ResourceExhausted, GoogleAPICallError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from state import Candidate
from prompts import get_translator_prompt

load_dotenv()
logger = logging.getLogger(__name__)


class Translation(BaseModel):
    translated: str


llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.1,
)
structured_llm = llm.with_structured_output(Translation)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=16),
    retry=retry_if_exception_type((
        ResourceExhausted,
        GoogleAPICallError,
    )),
    reraise=True,
)
def translate_repository(candidate: Candidate) -> None:
    messages = [
        SystemMessage(content=get_translator_prompt()),
        HumanMessage(content=candidate.explanation_en),
    ]

    result: Translation = structured_llm.invoke(messages)
    candidate.explanation_ka = result.translated


def translate_batch(candidates: list[Candidate]) -> list[Candidate]:
    for candidate in candidates:
        if not candidate.explanation_en:
            continue
        try:
            translate_repository(candidate)
        except Exception:
            logger.exception(f"[translator] failed on {candidate.full_name}")

    return candidates
