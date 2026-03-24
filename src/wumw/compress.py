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


def register(*commands: str):
    """Decorator to register a compressor for one or more command names."""
    def decorator(fn):
        for cmd in commands:
            _REGISTRY[cmd] = fn
        return fn
    return decorator


def _passthrough(lines: list[bytes]) -> list[bytes]:
    return lines


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


def compress(command: str, stdout: bytes) -> tuple[bytes, int, int]:
    """
    Apply compression to stdout.

    Returns (compressed_bytes, original_line_count, compressed_line_count).
    Falls back to passthrough if no compressor is registered for command.
    """
    lines = stdout.splitlines(keepends=True)
    original = len(lines)

    compressor = _REGISTRY.get(command, _passthrough)
    compressed_lines = compressor(lines)

    return b"".join(compressed_lines), original, len(compressed_lines)
