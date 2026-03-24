"""
Per-command compressor interface.

A compressor is a callable: (list[bytes]) -> list[bytes]
Register compressors via @register(["cmd", ...]).
compress() dispatches by command basename and returns compressed bytes + line counts.
"""

_REGISTRY: dict[str, callable] = {}


def register(*commands: str):
    """Decorator to register a compressor for one or more command names."""
    def decorator(fn):
        for cmd in commands:
            _REGISTRY[cmd] = fn
        return fn
    return decorator


def _passthrough(lines: list[bytes]) -> list[bytes]:
    return lines


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
