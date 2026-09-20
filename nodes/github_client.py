import os
import re
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

API = "https://api.github.com"
MIN_README_CHARS = 400
MAX_README_CHARS = 8000
MAX_FILE_CHARS = 3000
MAX_TREE_ENTRIES = 50

CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".cs", ".cpp", ".c", ".kt", ".swift"}
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "tests", "test", "__tests__", ".github", "migrations"}

_HEADERS = {
    "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}",
    "X-GitHub-Api-Version": "2022-11-28",
}


def parse_repo_url(url: str) -> tuple[str, str]:
    match = re.search(r"github\.com/([^/]+)/([^/#?]+)", url)
    if not match:
        raise ValueError(f"Not a GitHub repo URL: {url}")
    owner, repo = match.groups()
    return owner, repo.removesuffix(".git")


def _get(path: str, raw: bool = False):
    """Returns None on 404, raises on other HTTP errors."""
    headers = dict(_HEADERS)
    headers["Accept"] = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
    resp = requests.get(f"{API}{path}", headers=headers, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text if raw else resp.json()


def fetch_readme(owner: str, repo: str) -> str | None:
    text = _get(f"/repos/{owner}/{repo}/readme", raw=True)
    return text[:MAX_README_CHARS] if text else None


def fetch_tree(owner: str, repo: str) -> tuple[list[dict], str | None]:
    """Returns (tree entries, pushed_at). Uses the default branch."""
    info = _get(f"/repos/{owner}/{repo}")
    if not info:
        return [], None
    branch = info["default_branch"]
    data = _get(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    return (data["tree"] if data else []), info.get("pushed_at")


def fetch_file(owner: str, repo: str, path: str) -> str | None:
    text = _get(f"/repos/{owner}/{repo}/contents/{quote(path)}", raw=True)
    return text[:MAX_FILE_CHARS] if text else None


def format_tree(tree: list[dict]) -> str:
    """Top level plus one level down, capped, for the LLM."""
    entries = sorted(e for e in tree if e["path"].count("/") <= 1)
    lines = [e["path"] + ("/" if e["type"] == "tree" else "") for e in entries]
    return "\n".join(lines[:MAX_TREE_ENTRIES])


def pick_code_files(tree: list[dict], limit: int = 3) -> list[str]:
    """Substance first: the largest real source files, skipping tests/vendor/etc."""
    candidates = []
    for e in tree:
        if e["type"] != "blob":
            continue
        parts = e["path"].split("/")
        ext = os.path.splitext(parts[-1])[1].lower()
        if ext not in CODE_EXTENSIONS or any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        if not 200 <= e.get("size", 0) <= 100_000:
            continue
        candidates.append(e)
    candidates.sort(key=lambda e: e["size"], reverse=True)
    return [e["path"] for e in candidates[:limit]]


def fetch_repo_context(url: str) -> dict | None:
    """README + tree + top code files. None if README is missing/too short."""
    owner, repo = parse_repo_url(url)

    readme = fetch_readme(owner, repo)
    if not readme or len(readme.strip()) < MIN_README_CHARS:
        return None

    tree, pushed_at = fetch_tree(owner, repo)
    code_files = {}
    for path in pick_code_files(tree):
        content = fetch_file(owner, repo, path)
        if content:
            code_files[path] = content

    return {
        "readme": readme,
        "tree_text": format_tree(tree),
        "code_files": code_files,
        "pushed_at": pushed_at,
    }