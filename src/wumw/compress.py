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

CAT_TRUNCATE_LINES = 100  # E017: reduced from 500 to 100 with pagination hints
CAT_OUTLINE_THRESHOLD = 100  # For .py files, show outline instead of raw if > 100 lines

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


def _passthrough(lines: list[bytes], **kwargs) -> list[bytes]:
    return lines


def _compress_generic(lines: list[bytes], **kwargs) -> list[bytes]:
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
def _compress_grep(lines: list[bytes], **kwargs) -> list[bytes]:
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
def _compress_git(lines: list[bytes], **kwargs) -> list[bytes]:
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
def _compress_cat(lines: list[bytes], filename: str = None, total_lines: int = None) -> list[bytes]:
    """
    Strip blank lines and comment-only lines, truncate past CAT_TRUNCATE_LINES.
    Append pagination hint if truncated.
    For .py files > 100 lines, show class/def outline instead of raw content.
    """
    # For .py files with outline mode
    if filename and filename.endswith('.py') and total_lines and total_lines > CAT_OUTLINE_THRESHOLD:
        return _compress_cat_outline(lines, filename, total_lines)

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

    # Append pagination hint if truncated
    if total_lines and len(result) >= CAT_TRUNCATE_LINES and total_lines > CAT_TRUNCATE_LINES:
        truncated_lines = total_lines - len(result)
        hint = f"\n# wumw: {filename} has {total_lines} lines total — for more: tail -n +{len(result)+1} {filename} | head -100\n".encode()
        result.append(hint)

    return result


def _compress_cat_outline(lines: list[bytes], filename: str, total_lines: int) -> list[bytes]:
    """
    For Python files, emit a structural outline (class/def with line numbers) instead of raw content.
    Allows agent to navigate with sed -n 'N,Mp' FILE.
    """
    # Emit outline header
    result = [f"# wumw: Python outline for {filename} ({total_lines} lines)\n".encode()]

    # Extract class and def lines with line numbers
    line_num = 0
    for line in lines:
        line_num += 1
        stripped = line.strip()

        # Match class definitions
        if stripped.startswith(b'class '):
            result.append(f"  L{line_num}: {line.decode('utf-8', errors='ignore')}".encode())
        # Match method/function definitions (indented or at module level)
        elif stripped.startswith(b'def '):
            # Count leading spaces to show indentation
            indent = len(line) - len(line.lstrip())
            indent_marker = '    ' * (indent // 4) if indent > 0 else ''
            result.append(f"  L{line_num}: {indent_marker}{line.decode('utf-8', errors='ignore')}".encode())

    # Append navigation hint
    hint = f"\n# To read a section: sed -n 'START,ENDp' {filename}\n".encode()
    result.append(hint)

    return result


def compress(command: str, stdout: bytes, args: list[str] = None) -> tuple[bytes, int, int]:
    """
    Apply compression to stdout.

    Returns (compressed_bytes, original_line_count, compressed_line_count).
    Falls back to passthrough if no compressor is registered for command.

    For cat/read commands, extracts filename from args to enable pagination hints.
    """
    lines = stdout.splitlines(keepends=True)
    original = len(lines)

    compressor = _REGISTRY.get(command, _compress_generic)

    # For cat/read, pass filename and total lines to enable pagination hints
    if command in ('cat', 'read') and args:
        filename = args[-1]  # Typically the last argument
        compressed_lines = compressor(lines, filename=filename, total_lines=original)
    else:
        compressed_lines = compressor(lines, filename=None, total_lines=None)

    return b"".join(compressed_lines), original, len(compressed_lines)
