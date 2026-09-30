import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent
PENDING_FILE = BASE_DIR / "pending.json"
TMP_FILE = BASE_DIR / "pending.json.tmp"
SCHEDULED_JOB_LOCK_FILE = BASE_DIR / "scheduled_job.lock"

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
    processing_error: str | None = None
    source_checkpoints: dict[str, str] = field(default_factory=dict)


@contextmanager
def _file_lock(path: Path):
    """An OS-level lock shared by separate bot and CLI processes."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.seek(0)
                lock_file.write(b"\0")
                lock_file.flush()
            while True:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _locked_read(path: Path, default):
    with _file_lock(path):
        return _read_json(path, default)


def _locked_update(path: Path, default, update):
    with _file_lock(path):
        data = _read_json(path, default)
        result, changed = update(data)
        if changed:
            _write_json_atomic(path, data)
        return result


@contextmanager
def scheduled_job_lock():
    """Serialize CLI jobs so overlapping schedules cannot dispatch duplicates."""
    with _file_lock(SCHEDULED_JOB_LOCK_FILE):
        yield

class PollerState:
    def __init__(self, file_path: str = "state.json"):
        self.file_path = Path(file_path)
        if not self.file_path.is_absolute():
            self.file_path = BASE_DIR / self.file_path
        self._data = self._load()

    def _load(self) -> dict:
        return _locked_read(self.file_path, {})

    def get_last_checked(self, cluster_name: str) -> str | None:
        return self._load().get(cluster_name)

    def update_last_checked(self, cluster_name, timestamp: str):
        def update(data):
            changed = data.get(cluster_name) != timestamp
            data[cluster_name] = timestamp
            return None, changed
        _locked_update(self.file_path, {}, update)
        self._data = self._load()

    def is_seen(self, full_name: str) -> bool:
        seen = set(self._load().get("_seen_repos", []))
        return full_name in seen

    def mark_seen(self, full_name: str):
        self.mark_seen_batch([full_name])

    def mark_seen_batch(self, full_names: list[str]):
        def update(data):
            seen_list = data.setdefault("_seen_repos", [])
            seen_set = set(seen_list)
            updated = False
            for name in full_names:
                if name not in seen_set:
                    seen_list.append(name)
                    seen_set.add(name)
                    updated = True
            return None, updated
        _locked_update(self.file_path, {}, update)
        self._data = self._load()

    def commit_checkpoints(self, checkpoints: dict[str, str]) -> None:
        def update(data):
            changed = False
            for cluster, timestamp in checkpoints.items():
                if data.get(cluster) != timestamp:
                    data[cluster] = timestamp
                    changed = True
            return None, changed
        _locked_update(self.file_path, {}, update)
        self._data = self._load()


class PreferenceMemory:
    def __init__(self, file_path: str = "history.json"):
        self.file_path = Path(file_path)
        if not self.file_path.is_absolute():
            self.file_path = BASE_DIR / self.file_path
        self._data = self._load()

    def _load(self) -> list:
        return _locked_read(self.file_path, [])

    def add_decision(self, full_name, url, decision, description, reason, decision_id=None):
        def update(data):
            if decision_id and any(item.get("decision_id") == decision_id for item in data):
                return None, False
            data.append({
                "full_name": full_name,
                "url": url,
                "decision": decision,
                "description": description,
                "reason": reason,
                **({"decision_id": decision_id} if decision_id else {}),
            })
            return None, True
        _locked_update(self.file_path, [], update)
        self._data = self._load()

    def get_history(self) -> list:
        self._data = self._load()
        return list(self._data)


class RepoStatus:
    def __init__(self, file_path: str = "repo_status.json"):
        self.file_path = Path(file_path)
        if not self.file_path.is_absolute():
            self.file_path = BASE_DIR / self.file_path
        self._data = self._load()

    def _load(self) -> dict:
        return _locked_read(self.file_path, {})

    def add_entry(self, full_name: str, starred_at: str, selector_reason: str):
        def update(data):
            existing = data.get(full_name)
            if existing:
                existing["starred_at"] = starred_at
                existing["selector_reason"] = selector_reason
                return None, True
            data[full_name] = {
                "starred_at": starred_at,
                "selector_reason": selector_reason,
                "functional": None,
                "last_checked": None,
            }
            return None, True
        _locked_update(self.file_path, {}, update)
        self._data = self._load()

    def get_entry(self, full_name: str) -> dict | None:
        return self._load().get(full_name)

    def update_entry(self, full_name: str, functional: bool | None = None, last_checked: str | None = None):
        def update(data):
            if full_name not in data:
                return None, False
            if functional is not None:
                data[full_name]["functional"] = functional
            if last_checked is not None:
                data[full_name]["last_checked"] = last_checked
            return None, True
        _locked_update(self.file_path, {}, update)
        self._data = self._load()

    def remove_entry(self, full_name: str):
        def update(data):
            if full_name not in data:
                return None, False
            del data[full_name]
            return None, True
        _locked_update(self.file_path, {}, update)
        self._data = self._load()

    def get_all(self) -> dict:
        return dict(self._load())



class PendingRepos:
    def _load(self) -> dict:
        return _locked_read(PENDING_FILE, {})

    def save_atomic(self, data: dict) -> None:
        with _file_lock(PENDING_FILE):
            _write_json_atomic(PENDING_FILE, data)

    def add(self, id: str, data: dict) -> None:
        def update(pending):
            if id in pending:
                raise ValueError(f"pending callback ID already exists: {id}")
            pending[id] = {**data, "created_at": datetime.now(timezone.utc).isoformat()}
            return None, True
        _locked_update(PENDING_FILE, {}, update)

    def get(self, id: str) -> dict | None:
        return self._load().get(id)

    def pop(self, id: str) -> dict | None:
        """Atomically claim a callback so duplicate taps cannot run twice."""
        def update(pending):
            entry = pending.pop(id, None)
            return entry, entry is not None
        return _locked_update(PENDING_FILE, {}, update)

    def claim(self, id: str, stale_after: timedelta = timedelta(minutes=10)) -> dict | None:
        """Claim a callback for processing; abandoned claims become retryable."""
        now = datetime.now(timezone.utc)
        def update(pending):
            entry = pending.get(id)
            if entry is None:
                return None, False
            claimed_at = entry.get("claimed_at")
            if claimed_at:
                try:
                    claim_time = datetime.fromisoformat(claimed_at)
                    if claim_time.tzinfo is None:
                        claim_time = claim_time.replace(tzinfo=timezone.utc)
                except ValueError:
                    claim_time = now - stale_after
                if now - claim_time <= stale_after:
                    return None, False
            entry["claimed_at"] = now.isoformat()
            return dict(entry), True
        return _locked_update(PENDING_FILE, {}, update)

    def release(self, id: str) -> bool:
        def update(pending):
            entry = pending.get(id)
            if entry is None or "claimed_at" not in entry:
                return False, False
            del entry["claimed_at"]
            return True, True
        return _locked_update(PENDING_FILE, {}, update)

    def remove(self, id: str) -> bool:
        return self.pop(id) is not None

    def prune_old_entries(self, max_days: int = 30) -> int:
        now = datetime.now(timezone.utc)
        def update(pending):
            to_delete = []
            for id, item in pending.items():
                created_at_str = item.get("created_at")
                if created_at_str:
                    try:
                        created_at = datetime.fromisoformat(created_at_str)
                        if now - created_at > timedelta(days=max_days):
                            to_delete.append(id)
                    except ValueError:
                        continue
            for id in to_delete:
                del pending[id]
            return len(to_delete), bool(to_delete)
        return _locked_update(PENDING_FILE, {}, update)
