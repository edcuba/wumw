"""
Per-command compressor interface.

A compressor is a callable: (list[bytes]) -> list[bytes]
Register compressors via @register(["cmd", ...]).
compress() dispatches by command basename and returns compressed bytes + line counts.
"""

import re

_REGISTRY: dict[str, callable] = {}

# rg/grep: match line  path:lineno:content
_GREP_MATCH_RE = re.compile(rb'^(.+?):(\d+):(.*)')
# rg context line:      path:lineno-content
_GREP_CONTEXT_RE = re.compile(rb'^(.+?):(\d+)-(.*)')

MAX_MATCHES_PER_FILE = 5
MAX_CONTEXT_LINES = 2

CAT_TRUNCATE_LINES = 500

MAX_LOG_ENTRIES = 20

GENERIC_TRUNCATE_LINES = 200
GENERIC_REPEAT_THRESHOLD = 3  # collapse runs longer than this

# Comment prefixes recognised across common file types
_COMMENT_PREFIXES = (b'#', b'//', b'--', b'*', b'/*')


def register(*commands: str):
    """Decorator to register a compressor for one or more command names."""
    def decorator(fn):
        for cmd in commands:
            _REGISTRY[cmd] = fn
        return fn
    return decorator


def _passthrough(lines: list[bytes]) -> list[bytes]:
    return lines


def _compress_generic(lines: list[bytes]) -> list[bytes]:
    """
    Collapse consecutive repeated lines and truncate tail.

    Runs of more than GENERIC_REPEAT_THRESHOLD identical lines are replaced by
    a single copy followed by a note "# ... repeated N times".
    Output is then truncated to GENERIC_TRUNCATE_LINES.
    """
    result: list[bytes] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Count consecutive identical lines
        j = i + 1
        while j < len(lines) and lines[j] == line:
            j += 1
        run = j - i
        if run > GENERIC_REPEAT_THRESHOLD:
            result.append(line)
            note = f"# ... repeated {run} times\n".encode()
            result.append(note)
        else:
            result.extend(lines[i:j])
        i = j

    if len(result) > GENERIC_TRUNCATE_LINES:
        truncated = len(result) - GENERIC_TRUNCATE_LINES
        result = result[:GENERIC_TRUNCATE_LINES]
        result.append(f"# ... {truncated} more lines truncated\n".encode())

    return result


@register("rg", "grep")
def _compress_grep(lines: list[bytes]) -> list[bytes]:
    """
    Cap matches per file, deduplicate match content, limit context lines.

    Handles rg output format (path:lineno:content for matches,
    path:lineno-content for context, -- for group separators).
    Falls back to passthrough for unrecognised formats.
    """
    file_match_count: dict[bytes, int] = {}
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
            seen = file_seen_content.setdefault(filepath, set())
            content_key = content.strip()
            count = file_match_count.get(filepath, 0)

            if count >= MAX_MATCHES_PER_FILE or content_key in seen:
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
                if len(pre_ctx_buf) > MAX_CONTEXT_LINES:
                    pre_ctx_buf = pre_ctx_buf[-MAX_CONTEXT_LINES:]
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

    return result


_GIT_LOG_SHA_RE = re.compile(rb'^commit [0-9a-f]{7,40}')
_GIT_LOG_ONELINE_RE = re.compile(rb'^[0-9a-f]{7,40} ')


@register("git")
def _compress_git(lines: list[bytes]) -> list[bytes]:
    """
    Dispatch git subcommand compression based on output shape.

    For diff output (detected by 'diff --git' or '--- ' headers):
    strip index metadata lines, keep all hunks.
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
    # Oneline format: each line is one entry
    if lines and _GIT_LOG_ONELINE_RE.match(lines[0]):
        return lines[:MAX_LOG_ENTRIES]

    # Standard format: entries are blocks starting with "commit <sha>"
    result: list[bytes] = []
    entry_count = 0
    for line in lines:
        if _GIT_LOG_SHA_RE.match(line):
            if entry_count >= MAX_LOG_ENTRIES:
                break
            entry_count += 1
        result.append(line)
    return result


def _compress_git_diff(lines: list[bytes]) -> list[bytes]:
    """Strip index metadata lines (SHA noise), keep all hunk content."""
    result = []
    for line in lines:
        # index abc123..def456 100644 — pure metadata, no value to LLM
        if line.startswith(b'index ') and b'..' in line:
            continue
        result.append(line)
    return result


@register("cat", "read")
def _compress_cat(lines: list[bytes]) -> list[bytes]:
    """
    Strip blank lines and comment-only lines, truncate past CAT_TRUNCATE_LINES.
    """
    result: list[bytes] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_COMMENT_PREFIXES):
            continue
        result.append(line)
        if len(result) >= CAT_TRUNCATE_LINES:
            break
    return result


def compress(command: str, stdout: bytes) -> tuple[bytes, int, int]:
    """
    Apply compression to stdout.

    Returns (compressed_bytes, original_line_count, compressed_line_count).
    Falls back to passthrough if no compressor is registered for command.
    """
    lines = stdout.splitlines(keepends=True)
    original = len(lines)

    compressor = _REGISTRY.get(command, _compress_generic)
    compressed_lines = compressor(lines)

    return b"".join(compressed_lines), original, len(compressed_lines)
