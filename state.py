import json
from dataclasses import dataclass , field
from pathlib import Path


@dataclass
class Candidate:
    full_name: str
    url: str
    description: str
    stars: int
    language: str | None
    matched_clusters: list[str]

    is_accepted: bool | None = None
    validation_reason: str | None = None

    readme: str | None = None
    tree_text: str | None = None
    code_files: dict[str, str] = field(default_factory=dict)
    selector_accepted: bool | None = None
    selector_reason: str | None = None

    explanation_en: str | None = None
    explanation_ka: str | None = None

class PollerState:
    def __init__(self, file_path: str = "state.json"):
        self.file_path = Path(file_path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {}
        with open(self.file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get_last_checked(self, cluster_name: str) -> str | None:
        return self._data.get(cluster_name)

    def update_last_checked(self, cluster_name, timestamp: str):
        self._data[cluster_name] = timestamp
        self._save()

    def is_seen(self, full_name: str) -> bool:
        seen = set(self._data.get("_seen_repos", []))
        return full_name in seen

    def mark_seen(self, full_name: str):
        seen = self._data.setdefault("_seen_repos", [])
        if full_name not in seen:
            seen.append(full_name)
            self._save()

    def mark_seen_batch(self, full_names: list[str]):
        seen_list = self._data.setdefault("_seen_repos", [])
        seen_set = set(seen_list)
        updated = False
        for name in full_names:
            if name not in seen_set:
                seen_list.append(name)
                seen_set.add(name)
                updated = True
        if updated:
            self._save()


class PreferenceMemory:
    def __init__(self, file_path: str = "history.json"):
        self.file_path = Path(file_path)
        self._data = self._load()

    def _load(self) -> list:
        if not self.file_path.exists():
            return []
        with open(self.file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def add_decision(self, full_name, url, decision, description, reason):
        self._data.append({
            "full_name": full_name,
            "url": url,
            "decision": decision,
            "description": description,
            "reason": reason,
        })
        self._save()

    def get_history(self) -> list:
        return self._data


class RepoStatus:
    def __init__(self, file_path: str = "repo_status.json"):
        self.file_path = Path(file_path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {}
        with open(self.file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def add_entry(self, full_name: str, starred_at: str, selector_reason: str):
        self._data[full_name] = {
            "starred_at": starred_at,
            "selector_reason": selector_reason,
            "functional": None,
            "last_checked": None,
        }
        self._save()

    def get_entry(self, full_name: str) -> dict | None:
        return self._data.get(full_name)

    def update_entry(self, full_name: str, functional: bool | None = None, last_checked: str | None = None):
        if full_name in self._data:
            if functional is not None:
                self._data[full_name]["functional"] = functional
            if last_checked is not None:
                self._data[full_name]["last_checked"] = last_checked
            self._save()

    def remove_entry(self, full_name: str):
        if full_name in self._data:
            del self._data[full_name]
            self._save()

    def get_all(self) -> dict:
        return dict(self._data)