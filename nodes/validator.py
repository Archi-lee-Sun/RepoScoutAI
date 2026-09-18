import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
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

load_dotenv()
logger = logging.getLogger(__name__)

class ValidatorDecision(BaseModel):
    accept: bool
    reason: str


llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.1
)

def _format_candidate(candidate: Candidate) -> str:
    return (
        f"Name: {candidate.full_name}\n"
        f"URL: {candidate.url}\n"
        f"Description: {candidate.description}\n"
        f"Stars: {candidate.stars}\n"
        f"Language: {candidate.language}\n"
        f"Matched clusters: {', '.join(candidate.matched_clusters)}"
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=16),
    retry=retry_if_exception_type((
        ResourceExhausted,
        GoogleAPICallError,
    )),
    reraise=True,
)

def validate_candidate(candidate: Candidate, meta_prompt: str) :
    structured_llm = llm.with_structured_output(ValidatorDecision)
    fortmated_candidate = _format_candidate(candidate)
    messages = [
        SystemMessage(content=meta_prompt) ,
        HumanMessage(content=fortmated_candidate)
    ]

    result: ValidatorDecision = structured_llm.invoke(messages)
    candidate.is_accepted = result.accept
    candidate.validation_reason = result.reason


def validate_batch(candidates: list[Candidate], meta_prompt: str) -> list[Candidate]:
    for candidate in candidates:
        try:
            validate_candidate(candidate, meta_prompt)
        except Exception:
            logger.exception(f"[validator] failed on {candidate.full_name}")
    return candidates