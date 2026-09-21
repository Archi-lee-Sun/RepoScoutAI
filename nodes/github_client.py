import os
from urllib.parse import quote
import base64
import logging
from ..state import Candidate
import requests
from dotenv import load_dotenv

load_dotenv()

API = "https://api.github.com"
MIN_README_CHARS = 400
MAX_README_CHARS = 8000
MAX_FILE_CHARS = 3000
MAX_TREE_ENTRIES = 50

CONTENT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".cs", ".cpp", ".c", ".kt", ".swift",
    ".md", ".txt", ".yaml", ".yml", ".json", ".jinja", ".j2", ".prompt", ".toml",
}
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "tests", "test", "__tests__", ".github", "migrations"}
PROMPT_KEYWORDS = ("prompt", "system", "agent", "template", "instruction")
NOISE_NAMES = ("readme", "license", "changelog", "contributing", "code_of_conduct")

HEADERS = {
    "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}",
    "X-GitHub-Api-Version": "2022-11-28",
}

logger = logging.getLogger(__name__)


def get_readme(full_name : str) -> dict :
    url = f"https://api.github.com/repos/{full_name}/readme"
    try :
        response  = requests.get(url , headers=HEADERS , timeout=15)
        response.raise_for_status()
        data = response.json()

        content = base64.b64decode(data["content"]).decode("utf-8")
        size = data["size"]

        is_success = True if not size < 400 else False

        if not is_success :
            return {
                "is_success" : False ,
                "readme" : "" ,
            }
        else :
            return {
                "is_success" : True ,
                "readme" : content[:8000] ,
            }
    except Exception as e :
        logger.exception(f"[fetcher] failed to get readme for {full_name}")
        return {
            "is_success": False,
            "readme": "",
        }



def get_tree(full_name: str) -> list[dict]:
    url = f"https://api.github.com/repos/{full_name}/git/trees/HEAD?recursive=1"

    try:
        response = requests.get(url=url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()

        if data.get("truncated"):
            logger.warning(f"[fetcher] tree truncated for {full_name}")

        return data["tree"]
    except Exception:
        logger.exception(f"[fetcher] failed to get tree for {full_name}")
        return []


def format_tree_text(entries: list[dict]) -> str:
    lines = []
    for entry in entries:
        path = entry["path"]
        if path.count("/") > 1:
            continue
        if entry["type"] == "tree":
            path += "/"
        lines.append(path)

    lines.sort()
    return "\n".join(lines[:MAX_TREE_ENTRIES])


def pick_files(entries: list[dict], limit: int = 3) -> list[str]:
    candidates = []
    for entry in entries:
        if entry["type"] != "blob":
            continue

        path = entry["path"]
        parts = path.split("/")
        filename = parts[-1].lower()

        if os.path.splitext(filename)[1] not in CONTENT_EXTENSIONS:
            continue
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        if filename.startswith(NOISE_NAMES) or filename.endswith(("lock.json", "lock.yaml")):
            continue
        if not 200 <= entry.get("size", 0) <= 100_000:
            continue

        has_keyword = any(k in path.lower() for k in PROMPT_KEYWORDS)
        candidates.append((has_keyword, entry["size"], path))

    candidates.sort(reverse=True)
    return [path for _, _, path in candidates[:limit]]


def get_file(full_name: str, path: str) -> str:
    url = f"https://api.github.com/repos/{full_name}/contents/{quote(path)}"
    headers = {**HEADERS, "Accept": "application/vnd.github.raw+json"}

    try:
        response = requests.get(url=url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text[:MAX_FILE_CHARS]
    except Exception:
        logger.exception(f"[fetcher] failed to get file {path} in {full_name}")
        return ""


def fetch_batch(candidates: list[Candidate]) -> list[Candidate]:
    for candidate in candidates:
        if not candidate.is_accepted:
            continue

        try:
            readme = get_readme(candidate.full_name)
            if not readme["is_success"]:
                candidate.selector_accepted = False
                candidate.selector_reason = "README missing or too short"
                continue

            candidate.readme = readme["readme"]

            entries = get_tree(candidate.full_name)
            candidate.tree_text = format_tree_text(entries)

            for path in pick_files(entries):
                content = get_file(candidate.full_name, path)
                if content:
                    candidate.code_files[path] = content
        except Exception:
            logger.exception(f"[fetcher] failed on {candidate.full_name}")

    return candidates

