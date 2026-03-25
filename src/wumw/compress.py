"""
Per-command compressor interface.

A compressor is a callable: (list[bytes]) -> list[bytes]
Register compressors via @register(["cmd", ...]).
compress() dispatches by command basename and returns compressed bytes + line counts.
"""

import os
import re

_REGISTRY: dict[str, callable] = {}

# rg/grep: match line  path:lineno:content
_GREP_MATCH_RE = re.compile(rb'^(.+?):(\d+):(.*)')
# rg context line:      path:lineno-content
_GREP_CONTEXT_RE = re.compile(rb'^(.+?):(\d+)-(.*)')

MAX_MATCHES_PER_FILE = 5
MAX_CONTEXT_LINES = 2

CAT_TRUNCATE_LINES = 100  # E017: reduced from 500 to 100 with pagination hints
CAT_OUTLINE_THRESHOLD = 100  # For .py files, show outline instead of raw if > 100 lines
CAT_OUTLINE_CONTEXT_LOOKAHEAD = 12
CAT_OUTLINE_HINT_CHARS = 80

MAX_LOG_ENTRIES = 20

GIT_DIFF_REVIEW_MIN_HUNK_LINES = 20
GIT_DIFF_CONTEXT_LINES = 3

LISTING_MAX_ENTRIES = 40
LISTING_GROUP_SAMPLE_CAP = 4

GENERIC_TRUNCATE_LINES = 200
GENERIC_REPEAT_THRESHOLD = 3  # collapse runs longer than this

# Comment prefixes recognised across common file types
_COMMENT_PREFIXES = (b'#', b'//', b'--', b'*', b'/*')

_BINARY_SAMPLE_BYTES = 4096
_TEXT_WHITESPACE_BYTES = {9, 10, 12, 13}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def _rg_match_cap() -> int:
    return _env_int("WUMW_RG_CAP", MAX_MATCHES_PER_FILE)


def _rg_context_lines() -> int:
    return _env_int("WUMW_RG_CONTEXT_LINES", MAX_CONTEXT_LINES)


def _cat_truncate_lines() -> int:
    return _env_int("WUMW_CAT_LINES", CAT_TRUNCATE_LINES, minimum=1)


def _cat_outline_threshold() -> int:
    return _env_int("WUMW_CAT_OUTLINE_THRESHOLD", CAT_OUTLINE_THRESHOLD, minimum=1)


def _cat_outline_context_lookahead() -> int:
    return _env_int(
        "WUMW_CAT_OUTLINE_CONTEXT_LOOKAHEAD",
        CAT_OUTLINE_CONTEXT_LOOKAHEAD,
        minimum=1,
    )


def _cat_outline_hint_chars() -> int:
    return _env_int("WUMW_CAT_HINT_CHARS", CAT_OUTLINE_HINT_CHARS, minimum=4)


def _git_log_entries() -> int:
    return _env_int("WUMW_GIT_LOG_ENTRIES", MAX_LOG_ENTRIES)


def _git_diff_review_min_hunk_lines() -> int:
    return _env_int(
        "WUMW_GIT_DIFF_MIN_HUNK_LINES",
        GIT_DIFF_REVIEW_MIN_HUNK_LINES,
        minimum=1,
    )


def _git_diff_context_lines() -> int:
    return _env_int("WUMW_GIT_DIFF_CONTEXT_LINES", GIT_DIFF_CONTEXT_LINES)


def _listing_max_entries() -> int:
    return _env_int("WUMW_LISTING_MAX_ENTRIES", LISTING_MAX_ENTRIES, minimum=1)


def _listing_group_sample_cap() -> int:
    return _env_int(
        "WUMW_LISTING_GROUP_SAMPLE_CAP",
        LISTING_GROUP_SAMPLE_CAP,
        minimum=1,
    )


def _generic_truncate_lines() -> int:
    return _env_int("WUMW_GENERIC_LINES", GENERIC_TRUNCATE_LINES, minimum=1)


def _generic_repeat_threshold() -> int:
    return _env_int(
        "WUMW_GENERIC_REPEAT_THRESHOLD",
        GENERIC_REPEAT_THRESHOLD,
        minimum=1,
    )


def register(*commands: str):
    """Decorator to register a compressor for one or more command names."""
    def decorator(fn):
        for cmd in commands:
            _REGISTRY[cmd] = fn
        return fn
    return decorator


def _passthrough(lines: list[bytes], **kwargs) -> list[bytes]:
    return lines


def is_probably_binary(stdout: bytes) -> bool:
    """Heuristically classify stdout as binary so line-based compressors can skip it."""
    if not stdout:
        return False

    sample = stdout[:_BINARY_SAMPLE_BYTES]
    if b"\x00" in sample:
        return True

    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        pass

    non_text_bytes = 0
    for byte in sample:
        if byte in _TEXT_WHITESPACE_BYTES or 32 <= byte <= 126:
            continue
        non_text_bytes += 1

    return (non_text_bytes / len(sample)) > 0.30


def _compress_generic(lines: list[bytes], **kwargs) -> list[bytes]:
    """
    Collapse consecutive repeated lines and truncate tail.

    Runs of more than GENERIC_REPEAT_THRESHOLD identical lines are replaced by
    a single copy followed by a note "# ... repeated N times".
    Output is then truncated to GENERIC_TRUNCATE_LINES.
    """
    repeat_threshold = _generic_repeat_threshold()
    truncate_lines = _generic_truncate_lines()

    result: list[bytes] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Count consecutive identical lines
        j = i + 1
        while j < len(lines) and lines[j] == line:
            j += 1
        run = j - i
        if run > repeat_threshold:
            result.append(line)
            note = f"# ... repeated {run} times\n".encode()
            result.append(note)
        else:
            result.extend(lines[i:j])
        i = j

    if len(result) > truncate_lines:
        truncated = len(result) - truncate_lines
        result = result[:truncate_lines]
        result.append(f"# ... {truncated} more lines truncated\n".encode())

    return result


@register("rg", "grep")
def _compress_grep(lines: list[bytes], **kwargs) -> list[bytes]:
    """
    Cap matches per file, deduplicate match content, limit context lines.

    Handles rg output format (path:lineno:content for matches,
    path:lineno-content for context, -- for group separators).
    Falls back to passthrough for unrecognised formats.
    """
    match_cap = _rg_match_cap()
    context_lines = _rg_context_lines()

    file_match_count: dict[bytes, int] = {}
    file_total_matches: dict[bytes, int] = {}
    file_duplicate_omissions: dict[bytes, int] = {}
    file_cap_omissions: dict[bytes, int] = {}
    file_seen_content: dict[bytes, set] = {}

    result: list[bytes] = []
    # Context lines buffered while in a skipped-match block; flushed as
    # pre-context if the next match is kept, discarded if it is also skipped.
    pre_ctx_buf: list[bytes] = []
    in_skipped = False  # True after a match line was dropped

    for line in lines:
        raw = line.rstrip(b'\n\r')

        if raw == b'--':
            if not in_skipped:
                result.extend(pre_ctx_buf)
            pre_ctx_buf = []
            in_skipped = False
            result.append(line)
            continue

        m = _GREP_MATCH_RE.match(raw)
        if m:
            filepath, content = m.group(1), m.group(3)
            file_total_matches[filepath] = file_total_matches.get(filepath, 0) + 1
            seen = file_seen_content.setdefault(filepath, set())
            content_key = content.strip()
            count = file_match_count.get(filepath, 0)

            if content_key in seen:
                file_duplicate_omissions[filepath] = file_duplicate_omissions.get(filepath, 0) + 1
                in_skipped = True
                pre_ctx_buf = []
            elif count >= match_cap:
                file_cap_omissions[filepath] = file_cap_omissions.get(filepath, 0) + 1
                in_skipped = True
                pre_ctx_buf = []
            else:
                file_match_count[filepath] = count + 1
                seen.add(content_key)
                result.extend(pre_ctx_buf)
                pre_ctx_buf = []
                result.append(line)
                in_skipped = False
            continue

        c = _GREP_CONTEXT_RE.match(raw)
        if c:
            if in_skipped:
                # Post-context of a skipped match; save as potential pre-context
                # for the next match, trimmed to MAX_CONTEXT_LINES.
                pre_ctx_buf.append(line)
                if context_lines == 0:
                    pre_ctx_buf = []
                elif len(pre_ctx_buf) > context_lines:
                    pre_ctx_buf = pre_ctx_buf[-context_lines:]
            else:
                # Post-context of a kept match — emit immediately.
                result.append(line)
            continue

        # Unrecognised line (e.g. binary match notice, filename-only header).
        result.extend(pre_ctx_buf)
        pre_ctx_buf = []
        result.append(line)
        in_skipped = False

    if not in_skipped:
        result.extend(pre_ctx_buf)

    for filepath, total in file_total_matches.items():
        kept = file_match_count.get(filepath, 0)
        omitted = total - kept
        if omitted <= 0:
            continue

        reasons = []
        duplicate_omissions = file_duplicate_omissions.get(filepath, 0)
        cap_omissions = file_cap_omissions.get(filepath, 0)
        if duplicate_omissions:
            reasons.append(f"{duplicate_omissions} duplicate".encode())
        if cap_omissions:
            reasons.append(f"{cap_omissions} over cap".encode())

        summary = (
            b"# wumw: "
            + filepath
            + f" kept {kept}/{total} matches; {omitted} more matches omitted".encode()
        )
        if reasons:
            summary += b" (" + b", ".join(reasons) + b")"
        result.append(summary + b"\n")

    return result


_GIT_LOG_SHA_RE = re.compile(rb'^commit [0-9a-f]{7,40}')
_GIT_LOG_ONELINE_RE = re.compile(rb'^[0-9a-f]{7,40} ')


@register("git")
def _compress_git(lines: list[bytes], **kwargs) -> list[bytes]:
    """
    Dispatch git subcommand compression based on output shape.

    For diff output (detected by 'diff --git' or '--- ' headers):
    strip metadata noise and compress oversized hunks for review.
    For log output (detected by 'commit <sha>' lines): limit entries.
    """
    # Detect diff output
    is_diff = any(
        l.startswith(b'diff --git') or l.startswith(b'--- ')
        for l in lines[:20]
    )
    if is_diff:
        return _compress_git_diff(lines)

    # Detect log output (standard or oneline format)
    is_log = any(
        _GIT_LOG_SHA_RE.match(l) or _GIT_LOG_ONELINE_RE.match(l)
        for l in lines[:5]
    )
    if is_log:
        return _compress_git_log(lines)

    return lines


def _compress_git_log(lines: list[bytes]) -> list[bytes]:
    """Limit git log output to MAX_LOG_ENTRIES commits."""
    max_entries = _git_log_entries()

    # Oneline format: each line is one entry
    if lines and _GIT_LOG_ONELINE_RE.match(lines[0]):
        return lines[:max_entries]

    # Standard format: entries are blocks starting with "commit <sha>"
    result: list[bytes] = []
    entry_count = 0
    for line in lines:
        if _GIT_LOG_SHA_RE.match(line):
            if entry_count >= max_entries:
                break
            entry_count += 1
        result.append(line)
    return result


@register("fd", "find", "ls")
def _compress_listing(lines: list[bytes], **kwargs) -> list[bytes]:
    """
    Compress newline-delimited directory listings.

    The compressor is conservative:
    - deduplicate exact repeated paths
    - group large listings by extension and keep representative samples
    - pass through long-format `ls` output or other unrecognised layouts
    """
    listing_max_entries = _listing_max_entries()

    if not lines:
        return []

    entries = _parse_listing_entries(lines)
    if entries is None:
        return lines

    unique_entries: list[bytes] = []
    seen: set[bytes] = set()
    for entry in entries:
        if entry in seen:
            continue
        seen.add(entry)
        unique_entries.append(entry)

    if len(unique_entries) <= listing_max_entries:
        if len(unique_entries) == len(lines):
            return lines
        return [entry + b"\n" for entry in unique_entries]

    groups: dict[str, list[bytes]] = {}
    for entry in unique_entries:
        groups.setdefault(_listing_group(entry), []).append(entry)

    ordered_groups = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    sampled = _sample_listing_groups(ordered_groups)

    result: list[bytes] = [
        (
            f"# wumw: kept {sum(len(entries) for _, entries in sampled)}/{len(unique_entries)} "
            f"unique entries across {len(ordered_groups)} groups\n"
        ).encode()
    ]

    for label, sample_entries in sampled:
        total = len(groups[label])
        result.append(f"# {label}: {total} entries\n".encode())
        result.extend(entry + b"\n" for entry in sample_entries)
        omitted = total - len(sample_entries)
        if omitted > 0:
            result.append(f"# ... {omitted} more {label} entries omitted\n".encode())

    deduped_lines = [entry + b"\n" for entry in unique_entries]
    if len(result) >= len(deduped_lines):
        return deduped_lines[:listing_max_entries]

    return result


_LS_LONG_RE = re.compile(
    rb"^(?:[bcdlps-][rwxStTs-]{9}|[bcdlps-][rwxStTs-]{10}|\S+[+@]?)\s+\d+\s+"
)


def _parse_listing_entries(lines: list[bytes]) -> list[bytes] | None:
    entries: list[bytes] = []

    for line in lines:
        raw = line.rstrip(b"\n\r")
        if not raw:
            continue

        stripped = raw.strip()
        if not stripped:
            continue

        if stripped.startswith(b"total "):
            return None

        if _LS_LONG_RE.match(stripped):
            return None

        if stripped.endswith(b":"):
            # Recursive `ls` directory section header, not a path entry.
            continue

        entries.append(stripped)

    return entries


def _listing_group(entry: bytes) -> str:
    decoded = entry.decode("utf-8", errors="ignore")
    if decoded.endswith("/"):
        return "[dir]"

    basename = decoded.rstrip("/").rsplit("/", 1)[-1]
    if "." not in basename or basename.endswith("."):
        return "[no extension]"

    suffix = "." + basename.rsplit(".", 1)[-1]
    return suffix.lower()


def _sample_listing_groups(groups: list[tuple[str, list[bytes]]]) -> list[tuple[str, list[bytes]]]:
    listing_max_entries = _listing_max_entries()
    group_sample_cap = _listing_group_sample_cap()
    indexes = {label: 0 for label, _ in groups}
    samples = {label: [] for label, _ in groups}
    remaining = listing_max_entries

    while remaining > 0:
        progress = False
        for label, entries in groups:
            idx = indexes[label]
            if idx >= len(entries):
                continue
            if idx >= group_sample_cap:
                continue

            samples[label].append(entries[idx])
            indexes[label] += 1
            remaining -= 1
            progress = True
            if remaining == 0:
                break

        if not progress:
            break

    return [(label, samples[label]) for label, _ in groups if samples[label]]


def _compress_git_diff(lines: list[bytes]) -> list[bytes]:
    """
    Strip diff metadata noise and shrink oversized hunks for review work.

    File headers and hunk headers are preserved. Large unchanged stretches
    inside hunks are replaced with an omission marker while keeping a few
    lines of local context around each change cluster.
    """
    result: list[bytes] = []
    hunk_body: list[bytes] = []
    in_hunk = False

    def flush_hunk() -> None:
        if not hunk_body:
            return
        result.extend(_compress_git_hunk(hunk_body))
        hunk_body.clear()

    for line in lines:
        # index abc123..def456 100644 — pure metadata, no value to LLM
        if line.startswith(b'index ') and b'..' in line:
            continue

        if line.startswith(b'@@'):
            flush_hunk()
            result.append(line)
            in_hunk = True
            continue

        if in_hunk:
            if line.startswith(b'diff --git'):
                flush_hunk()
                result.append(line)
                in_hunk = False
            else:
                hunk_body.append(line)
            continue

        result.append(line)

    flush_hunk()
    return result


def _compress_git_hunk(lines: list[bytes]) -> list[bytes]:
    """Collapse large unchanged spans inside a single diff hunk."""
    min_hunk_lines = _git_diff_review_min_hunk_lines()
    context_lines = _git_diff_context_lines()

    if len(lines) <= min_hunk_lines:
        return lines

    interesting = _git_hunk_interesting_indexes(lines)
    if not interesting:
        return lines

    keep_indexes: set[int] = set()
    for idx in interesting:
        start = max(0, idx - context_lines)
        end = min(len(lines), idx + context_lines + 1)
        keep_indexes.update(range(start, end))

    if len(keep_indexes) >= len(lines):
        return lines

    compressed: list[bytes] = []
    idx = 0
    while idx < len(lines):
        if idx in keep_indexes:
            compressed.append(lines[idx])
            idx += 1
            continue

        gap_start = idx
        while idx < len(lines) and idx not in keep_indexes:
            idx += 1
        gap = lines[gap_start:idx]

        if all(line.startswith(b' ') for line in gap):
            compressed.append(
                f"# ... {len(gap)} unchanged lines omitted ...\n".encode()
            )
        else:
            compressed.extend(gap)

    return compressed


def _git_hunk_interesting_indexes(lines: list[bytes]) -> list[int]:
    """Return indexes that represent actual change content inside a hunk."""
    interesting: set[int] = set()

    for idx, line in enumerate(lines):
        if line.startswith((b'+', b'-')):
            interesting.add(idx)
            continue

        if line.startswith(b'\\ '):
            interesting.add(idx)
            if idx > 0:
                interesting.add(idx - 1)

    return sorted(interesting)


@register("cat", "read")
def _compress_cat(lines: list[bytes], filename: str = None, total_lines: int = None) -> list[bytes]:
    """
    Strip blank lines and comment-only lines, truncate past CAT_TRUNCATE_LINES.
    Append pagination hint if truncated.
    For .py files > 100 lines, show class/def outline instead of raw content.
    """
    cat_lines = _cat_truncate_lines()
    outline_threshold = _cat_outline_threshold()

    # For .py files with outline mode
    if filename and filename.endswith('.py') and total_lines and total_lines > outline_threshold:
        return _compress_cat_outline(lines, filename, total_lines)

    result: list[bytes] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_COMMENT_PREFIXES):
            continue
        result.append(line)
        if len(result) >= cat_lines:
            break

    # Append pagination hint if truncated
    if total_lines and len(result) >= cat_lines and total_lines > cat_lines:
        truncated_lines = total_lines - len(result)
        hint = (
            f"\n# wumw: {filename} has {total_lines} lines total — "
            f"for more: tail -n +{len(result)+1} {filename} | head -{cat_lines}\n"
        ).encode()
        result.append(hint)

    return result


def _compress_cat_outline(lines: list[bytes], filename: str, total_lines: int) -> list[bytes]:
    """
    For Python files, emit a structural outline (class/def with line numbers) instead of raw content.
    Allows agent to navigate with sed -n 'N,Mp' FILE.
    """
    result = [f"# wumw: Python outline for {filename} ({total_lines} lines)\n".encode()]
    pending_decorators: list[tuple[int, bytes]] = []

    for idx, line in enumerate(lines):
        line_num = idx + 1
        stripped = line.strip()

        if stripped.startswith(b'@'):
            pending_decorators.append((line_num, line))
            continue

        if not _is_python_outline_entry(stripped):
            pending_decorators.clear()
            continue

        indent_marker = _python_indent_marker(line)
        for decorator_line_num, decorator_line in pending_decorators:
            result.append(
                f"  L{decorator_line_num}: {indent_marker}{_outline_entry_text(decorator_line)}\n".encode()
            )
        pending_decorators.clear()

        entry = f"  L{line_num}: {indent_marker}{_outline_entry_text(line)}"
        hint = _python_outline_context_hint(lines, idx)
        if hint:
            entry += f"  # {hint}"
        result.append(f"{entry}\n".encode())

    hint = f"\n# To read a section: sed -n 'START,ENDp' {filename}\n".encode()
    result.append(hint)

    return result


def _is_python_outline_entry(stripped: bytes) -> bool:
    return stripped.startswith((b'class ', b'def ', b'async def '))


def _python_indent_marker(line: bytes) -> str:
    indent = len(line) - len(line.lstrip())
    return '    ' * (indent // 4) if indent > 0 else ''


def _decode_outline_line(line: bytes) -> str:
    return line.decode('utf-8', errors='ignore').rstrip('\n\r')


def _outline_entry_text(line: bytes) -> str:
    return _decode_outline_line(line).lstrip()


def _truncate_outline_hint(text: str) -> str:
    hint_chars = _cat_outline_hint_chars()
    compact = " ".join(text.split())
    if len(compact) <= hint_chars:
        return compact
    return compact[:hint_chars - 3].rstrip() + "..."


def _python_outline_context_hint(lines: list[bytes], start_idx: int) -> str | None:
    base_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    context_lookahead = _cat_outline_context_lookahead()
    line_limit = min(len(lines), start_idx + 1 + context_lookahead)
    in_signature = not lines[start_idx].rstrip().endswith(b':')

    for idx in range(start_idx + 1, line_limit):
        line = lines[idx]
        stripped = line.strip()

        if not stripped:
            continue

        if in_signature:
            if stripped.endswith(b':'):
                in_signature = False
            continue

        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            break

        if stripped.startswith(b'#'):
            continue

        if stripped.startswith(b'@'):
            continue

        doc_hint = _python_docstring_hint(lines, idx, base_indent)
        if doc_hint:
            return doc_hint

        return _truncate_outline_hint(_decode_outline_line(line).strip())

    return None


def _python_docstring_hint(lines: list[bytes], start_idx: int, base_indent: int) -> str | None:
    context_lookahead = _cat_outline_context_lookahead()
    first_line = _decode_outline_line(lines[start_idx]).strip()
    match = re.match(r'^([rRuUbBfF]*)(["\']{3}|["\'])', first_line)
    if not match:
        return None

    delimiter = match.group(2)
    prefix_len = len(match.group(1)) + len(delimiter)
    remainder = first_line[prefix_len:]

    if remainder:
        end_pos = remainder.find(delimiter)
        if end_pos != -1:
            content = remainder[:end_pos].strip()
            if content:
                return f"doc: {_truncate_outline_hint(content)}"
        else:
            content = remainder.strip()
            if content:
                return f"doc: {_truncate_outline_hint(content)}"

    line_limit = min(len(lines), start_idx + context_lookahead)
    for idx in range(start_idx + 1, line_limit):
        text = _decode_outline_line(lines[idx]).strip()
        if delimiter in text:
            text = text.split(delimiter, 1)[0].strip()
        if text:
            return f"doc: {_truncate_outline_hint(text)}"

        indent = len(lines[idx]) - len(lines[idx].lstrip())
        if indent <= base_indent:
            break

    return "docstring"


def compress(command: str, stdout: bytes, args: list[str] = None) -> tuple[bytes, int, int]:
    """
    Apply compression to stdout.

    Returns (compressed_bytes, original_line_count, compressed_line_count).
    Falls back to passthrough if no compressor is registered for command.

    For cat/read commands, extracts filename from args to enable pagination hints.
    """
    lines = stdout.splitlines(keepends=True)
    original = len(lines)

    if is_probably_binary(stdout):
        return stdout, original, original

    compressor = _REGISTRY.get(command, _compress_generic)

    # For cat/read, pass filename and total lines to enable pagination hints
    if command in ('cat', 'read') and args:
        filename = args[-1]  # Typically the last argument
        compressed_lines = compressor(lines, filename=filename, total_lines=original)
    else:
        compressed_lines = compressor(lines, filename=None, total_lines=None)

    return b"".join(compressed_lines), original, len(compressed_lines)
