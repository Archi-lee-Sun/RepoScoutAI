import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging

from dotenv import load_dotenv
from pydantic import BaseModel, Field
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
from candidate_failures import is_temporary_candidate_failure, record_candidate_failure
from prompts import get_selector_prompt

load_dotenv()
logger = logging.getLogger(__name__)


class SelectorDecision(BaseModel):
    accept: bool
    score: int = Field(ge=0, le=100, description="Calibrated relevance and practical-value score")
    reason: str


llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.1,
    max_retries=1,
)
structured_llm = llm.with_structured_output(SelectorDecision)


def _format_candidate(candidate: Candidate) -> str:
    tree = candidate.tree_text or "none available"

    if candidate.code_files:
        files = "\n\n".join(
            f"--- {path} ---\n{content}"
            for path, content in candidate.code_files.items()
        )
    else:
        files = "none available"

    return (
        f"Name: {candidate.full_name}\n"
        f"URL: {candidate.url}\n"
        f"Description: {candidate.description}\n"
        f"Stars: {candidate.stars}\n"
        f"Language: {candidate.language}\n"
        f"Matched clusters: {', '.join(candidate.matched_clusters)}\n\n"
        f"=== README ===\n{candidate.readme}\n\n"
        f"=== FILE TREE ===\n{tree}\n\n"
        f"=== KEY FILES ===\n{files}"
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
def select_candidate(candidate: Candidate, meta_prompt: str) -> None:
    messages = [
        SystemMessage(content=get_selector_prompt(meta_prompt)),
        HumanMessage(content=_format_candidate(candidate)),
    ]

    result: SelectorDecision = structured_llm.invoke(messages)
    candidate.selector_accepted = result.accept
    candidate.selector_score = result.score
    candidate.selector_reason = result.reason


def select_batch(candidates: list[Candidate], meta_prompt: str) -> list[Candidate]:
    for candidate in candidates:
        if candidate.processing_error or not candidate.is_accepted:
            continue
        if not candidate.readme:
            continue
        if candidate.selector_accepted is not None:
            continue

        try:
            select_candidate(candidate, meta_prompt)
        except Exception as exc:
            if not is_temporary_candidate_failure(exc):
                raise
            record_candidate_failure(candidate, "selector", exc)
    return candidates
