import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
from datetime import datetime, timezone, timedelta
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

from state import RepoStatus
from prompts import get_cleanup_prompt
from github_client import (
    get_readme,
    get_tree,
    format_tree_text,
    pick_files,
    get_file,
    get_repo_info,
    unstar_repo,
)

load_dotenv()
logger = logging.getLogger(__name__)


class CleanupDecision(BaseModel):
    functional: bool
    reason: str


llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0.1,
)
structured_llm = llm.with_structured_output(CleanupDecision)


def _format_cleanup_input(
    full_name: str,
    silence_days: int,
    selector_reason: str,
    readme: str,
    tree_text: str,
    code_files: dict[str, str],
) -> str:
    if code_files:
        files = "\n\n".join(
            f"--- {path} ---\n{content}"
            for path, content in code_files.items()
        )
    else:
        files = "none available"

    return (
        f"Name: {full_name}\n"
        f"Silence duration: {silence_days} days since last commit/push\n"
        f"Original acceptance reason: {selector_reason or 'No reason provided'}\n\n"
        f"=== README ===\n{readme}\n\n"
        f"=== FILE TREE ===\n{tree_text}\n\n"
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
def check_repo(full_name: str, entry: dict, repo_status: RepoStatus) -> None:
    if entry.get("functional") is True or entry.get("functional") is False:
        return

    repo_info = get_repo_info(full_name)
    pushed_at_str = repo_info.get("pushed_at")
    if not pushed_at_str:
        logger.warning(f"[cleanup] could not retrieve pushed_at for {full_name}")
        return

    pushed_dt = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    silence_delta = now - pushed_dt

    if silence_delta < timedelta(days=21):
        return

    silence_days = silence_delta.days
    readme_data = get_readme(full_name)
    readme = readme_data.get("readme", "") or "none available"

    entries = get_tree(full_name)
    tree_text = format_tree_text(entries) or "none available"

    code_files: dict[str, str] = {}
    for path in pick_files(entries):
        content = get_file(full_name, path)
        if content:
            code_files[path] = content

    content = _format_cleanup_input(
        full_name=full_name,
        silence_days=silence_days,
        selector_reason=entry.get("selector_reason", ""),
        readme=readme,
        tree_text=tree_text,
        code_files=code_files,
    )

    messages = [
        SystemMessage(content=get_cleanup_prompt()),
        HumanMessage(content=content),
    ]

    result: CleanupDecision = structured_llm.invoke(messages)
    now_iso = now.isoformat()

    if result.functional:
        repo_status.update_entry(full_name, functional=True, last_checked=now_iso)
    else:
        unstar_repo(full_name)
        repo_status.update_entry(full_name, functional=False, last_checked=now_iso)


def run_cleanup(file_path: str = "repo_status.json") -> None:
    repo_status = RepoStatus(file_path)
    all_entries = repo_status.get_all()

    for full_name, entry in list(all_entries.items()):
        try:
            check_repo(full_name, entry, repo_status)
        except Exception:
            logger.exception(f"[cleanup] failed on {full_name}")
