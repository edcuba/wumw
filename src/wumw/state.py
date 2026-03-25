import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS = 30 * 60


def find_repo_root() -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _context_root() -> tuple[Path, bool]:
    repo_root = find_repo_root()
    if repo_root is not None:
        return repo_root, True
    return Path.cwd().resolve(), False


def _fallback_state_dir(context_root: Path, in_git_repo: bool) -> Path:
    base = Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    )
    digest = hashlib.sha1(str(context_root).encode("utf-8")).hexdigest()[:12]
    scope = "repo" if in_git_repo else "cwd"
    return base / "wumw" / f"{scope}-{context_root.name}-{digest}"


def _temp_state_dir(context_root: Path, in_git_repo: bool) -> Path:
    digest = hashlib.sha1(str(context_root).encode("utf-8")).hexdigest()[:12]
    scope = "repo" if in_git_repo else "cwd"
    return Path(tempfile.gettempdir()) / "wumw" / f"{scope}-{context_root.name}-{digest}"


def _ensure_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def get_state_dir() -> Path:
    if configured := os.environ.get("WUMW_HOME"):
        configured_path = Path(configured).expanduser()
        configured_path.mkdir(parents=True, exist_ok=True)
        return configured_path

    context_root, in_git_repo = _context_root()
    if in_git_repo:
        repo_state_dir = context_root / ".wumw"
        if _ensure_dir(repo_state_dir):
            return repo_state_dir

    fallback_dir = _fallback_state_dir(context_root, in_git_repo)
    if _ensure_dir(fallback_dir):
        return fallback_dir

    temp_dir = _temp_state_dir(context_root, in_git_repo)
    if _ensure_dir(temp_dir):
        return temp_dir

    raise OSError("unable to create a writable wumw state directory")


def _session_file_path() -> Path:
    return get_state_dir() / "session"


def _session_idle_timeout_seconds() -> int:
    value = os.environ.get("WUMW_SESSION_IDLE_TIMEOUT_SECONDS")
    if value is None:
        return DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS
    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS
    return max(0, parsed)


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_session_record(session_file: Path) -> dict | None:
    if not session_file.exists():
        return None

    raw = session_file.read_text().strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return parsed

    # Backward compatibility with the original plain-text session id format.
    return {"session_id": raw}


def _new_session_record(now: datetime, *, context_root: Path, in_git_repo: bool) -> dict:
    now_iso = now.isoformat()
    return {
        "session_id": str(uuid.uuid4()),
        "started_at": now_iso,
        "last_used_at": now_iso,
        "cwd": str(Path.cwd().resolve()),
        "context_root": str(context_root),
        "in_git_repo": in_git_repo,
    }


def get_session_info() -> dict:
    now = datetime.now(timezone.utc)
    context_root, in_git_repo = _context_root()
    current_cwd = str(Path.cwd().resolve())

    if session_id := os.environ.get("WUMW_SESSION"):
        return {
            "session_id": session_id,
            "started_at": now.isoformat(),
            "last_used_at": now.isoformat(),
            "cwd": current_cwd,
            "context_root": str(context_root),
            "in_git_repo": in_git_repo,
            "source": "env",
        }

    if codex_thread_id := os.environ.get("CODEX_THREAD_ID"):
        return {
            "session_id": codex_thread_id,
            "started_at": now.isoformat(),
            "last_used_at": now.isoformat(),
            "cwd": current_cwd,
            "context_root": str(context_root),
            "in_git_repo": in_git_repo,
            "source": "codex_thread",
        }

    session_file = _session_file_path()
    record = _load_session_record(session_file)
    timeout = timedelta(seconds=_session_idle_timeout_seconds())
    current_context_root = str(context_root)

    should_rotate = record is None
    if record is not None:
        last_used_at = _parse_iso8601(record.get("last_used_at"))
        if last_used_at is None:
            started_at = _parse_iso8601(record.get("started_at"))
            last_used_at = started_at
        if last_used_at is None:
            should_rotate = True
        elif timeout.total_seconds() > 0 and now - last_used_at > timeout:
            should_rotate = True
        elif record.get("context_root") not in (None, current_context_root):
            should_rotate = True

    if should_rotate:
        record = _new_session_record(
            now,
            context_root=context_root,
            in_git_repo=in_git_repo,
        )
    else:
        record["last_used_at"] = now.isoformat()
        record["cwd"] = current_cwd
        record["context_root"] = current_context_root
        record["in_git_repo"] = in_git_repo
        if "started_at" not in record:
            record["started_at"] = now.isoformat()

    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(json.dumps(record, sort_keys=True))
    return record
