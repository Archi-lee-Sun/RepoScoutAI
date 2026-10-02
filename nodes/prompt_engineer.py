import logging
import os
import sys
from dotenv import load_dotenv

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NODES_DIR = os.path.dirname(__file__)
for path in (ROOT_DIR, NODES_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from google import genai
from google.genai import errors

try:
  from nodes.poller import INTERESTS
  from nodes.prompts import get_validator_briefing_prompt
  from nodes.state import PreferenceMemory
except ModuleNotFoundError:
  from poller import INTERESTS
  from prompts import get_validator_briefing_prompt
  from state import PreferenceMemory
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(
    api_key=GOOGLE_API_KEY,
    http_options=genai.types.HttpOptions(
        retry_options=genai.types.HttpRetryOptions(attempts=1),
    ),
)

MODEL_NAME = "gemini-3.1-flash-lite"


class EmptyLLMResponseError(Exception):
  pass


def _format_history(history: list) -> str:
  if not history:
    return "No accept/reject history yet — this is the first cycle."

  lines = []
  for entry in history:
    reason = entry.get("reason") or "no reason given"
    lines.append(
        f"- [{entry['decision'].upper()}] {entry['full_name']}: "
        f"{entry['description']} (reason: {reason})"
    )

  return "\n".join(lines)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=16),
    retry=retry_if_exception_type((
        errors.ClientError,   # covers 429 rate-limit among other 4xx
        errors.ServerError,   # covers 5xx transient failures
        EmptyLLMResponseError,
    )),
    reraise=True,
)
def generate_meta_prompt(preference_memory: PreferenceMemory) -> str:
  history_data = preference_memory.get_history()
  formatted_history = _format_history(history_data)

  prompt_text = get_validator_briefing_prompt(INTERESTS, formatted_history)

  response = client.models.generate_content(
      model=MODEL_NAME, contents=prompt_text
  )
  meta_prompt = response.text.strip() if response.text else ""

  if not meta_prompt:
    raise EmptyLLMResponseError("The LLM returned empty text")

  return meta_prompt
