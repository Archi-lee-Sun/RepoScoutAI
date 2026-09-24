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
from prompts import get_explainer_prompt

load_dotenv()
logger = logging.getLogger(__name__)

class Explainer(BaseModel) :
    explanation: str 

llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.1
)
structured_llm = llm.with_structured_output(Explainer)



def _format_candidate(candidate: Candidate) -> str:
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
        f"=== KEY FILES ===\n{files}\n\n"
        f"=== SELECTOR REASON ===\n{candidate.selector_reason}"
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
def explain_repository(candidate : Candidate) :
    messages = [
        SystemMessage(content=get_explainer_prompt()) ,
        HumanMessage(content=_format_candidate(candidate))
    ]

    result : Explainer = structured_llm.invoke(messages)
    candidate.explanation_en = result.explanation

        
def explain_batch(candidates: list[Candidate]) -> list[Candidate]: 
    for candidate in candidates :
        if not candidate.selector_accepted:
            continue
        try :
            explain_repository(candidate)
        except Exception :
            logger.exception(
                "Failed to explain repository: %s",
                candidate.full_name,
            )


    return candidates