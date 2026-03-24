"""
Tests for wumw — passthrough wrapper and compressors.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the package is importable from the src layout
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from wumw.compress import (
    compress,
    _compress_cat,
    _compress_generic,
    _compress_grep,
    _compress_git,
    _compress_git_diff,
    _compress_git_log,
    MAX_MATCHES_PER_FILE,
    MAX_CONTEXT_LINES,
    CAT_TRUNCATE_LINES,
    MAX_LOG_ENTRIES,
    GENERIC_TRUNCATE_LINES,
    GENERIC_REPEAT_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def lines(*strs):
    """Return list[bytes] with newlines, as splitlines(keepends=True) would."""
    return [s.encode() + b"\n" for s in strs]


def join(line_list):
    return b"".join(line_list)


WUMW = str(Path(__file__).parent.parent / ".venv" / "bin" / "wumw")
REPO_ROOT = Path(__file__).parent.parent
# Logs are written to <repo_root>/.wumw/sessions/ (cli uses git rev-parse)
LOG_DIR = REPO_ROOT / ".wumw" / "sessions"


def run_wumw(*args, env=None, input_=None):
    """Run the wumw CLI, return CompletedProcess."""
    cmd = [WUMW] + list(args)
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, capture_output=True, env=merged_env, input=input_)


# ---------------------------------------------------------------------------
# 1. Passthrough — exit code, stdout, stderr forwarded correctly
# ---------------------------------------------------------------------------

class TestPassthrough:
    def test_exit_code_zero(self, tmp_path):
        env = {"WUMW_SESSION": "test-passthrough"}
        r = run_wumw("true", env=env)
        assert r.returncode == 0

    def test_exit_code_nonzero(self, tmp_path):
        env = {"WUMW_SESSION": "test-passthrough"}
        r = run_wumw("false", env=env)
        assert r.returncode == 1

    def test_exit_code_custom(self):
        env = {"WUMW_SESSION": "test-passthrough"}
        r = run_wumw("sh", "-c", "exit 42", env=env)
        assert r.returncode == 42

    def test_stdout_forwarded(self):
        env = {"WUMW_SESSION": "test-passthrough-stdout"}
        r = run_wumw("sh", "-c", "printf hello", env=env)
        assert b"hello" in r.stdout

    def test_stderr_forwarded(self):
        env = {"WUMW_SESSION": "test-passthrough-stderr"}
        r = run_wumw("sh", "-c", "printf err >&2", env=env)
        assert b"err" in r.stderr

    def test_stderr_not_in_stdout(self):
        env = {"WUMW_SESSION": "test-passthrough-sep"}
        r = run_wumw("sh", "-c", "printf out; printf err >&2", env=env)
        assert b"out" in r.stdout
        assert b"err" in r.stderr
        assert b"err" not in r.stdout


# ---------------------------------------------------------------------------
# 2. rg/grep compressor — caps per file, deduplication
# ---------------------------------------------------------------------------

class TestGrepCompressor:
    def _make_match(self, filepath, lineno, content):
        return f"{filepath}:{lineno}:{content}".encode() + b"\n"

    def _make_context(self, filepath, lineno, content):
        return f"{filepath}:{lineno}-{content}".encode() + b"\n"

    def test_caps_matches_per_file(self):
        """More than MAX_MATCHES_PER_FILE matches for one file should be dropped."""
        input_lines = [
            self._make_match("file.py", i, f"match {i}")
            for i in range(MAX_MATCHES_PER_FILE + 5)
        ]
        result = _compress_grep(input_lines)
        # Count how many match lines remain for file.py
        count = sum(1 for l in result if b"file.py:" in l and b"-" not in l.split(b":")[1])
        assert count <= MAX_MATCHES_PER_FILE

    def test_deduplicates_match_content(self):
        """Identical content in same file should appear only once."""
        input_lines = [
            self._make_match("a.py", 1, "duplicate content"),
            self._make_match("a.py", 2, "duplicate content"),
            self._make_match("a.py", 3, "duplicate content"),
        ]
        result = _compress_grep(input_lines)
        match_lines = [l for l in result if b"a.py:" in l]
        assert len(match_lines) == 1

    def test_different_files_independent_caps(self):
        """Each file has its own cap; matches from different files kept separately."""
        input_lines = []
        for f in ("a.py", "b.py"):
            for i in range(MAX_MATCHES_PER_FILE):
                input_lines.append(self._make_match(f, i, f"hit {i}"))
        result = _compress_grep(input_lines)
        for f in (b"a.py", b"b.py"):
            count = sum(1 for l in result if l.startswith(f + b":"))
            assert count == MAX_MATCHES_PER_FILE

    def test_separator_lines_pass_through(self):
        """'--' separator lines are preserved when not inside a skipped block."""
        input_lines = [
            self._make_match("a.py", 1, "hit"),
            b"--\n",
            self._make_match("b.py", 1, "hit"),
        ]
        result = _compress_grep(input_lines)
        assert b"--\n" in result

    def test_passthrough_unrecognised_format(self):
        """Lines that don't match match/context format pass through unchanged."""
        plain = [b"just a plain line\n", b"another line\n"]
        result = _compress_grep(plain)
        assert result == plain

    def test_empty_input(self):
        assert _compress_grep([]) == []

    def test_context_lines_kept_for_kept_match(self):
        """Context lines after a kept match should be emitted."""
        import re
        _ctx_re = re.compile(rb'^(.+?):(\d+)-(.*)')
        input_lines = [
            self._make_match("f.py", 5, "match here"),
            self._make_context("f.py", 6, "context line"),
        ]
        result = _compress_grep(input_lines)
        ctx = [l for l in result if _ctx_re.match(l.rstrip(b'\n\r'))]
        assert len(ctx) == 1

    def test_context_lines_dropped_for_skipped_match(self):
        """Context lines after a skipped (duplicate) match should be dropped."""
        dup_content = "same content"
        input_lines = [
            self._make_match("f.py", 1, dup_content),  # kept
            self._make_match("f.py", 2, dup_content),  # skipped (dup)
            self._make_context("f.py", 3, "ctx after skip"),  # should be dropped
        ]
        result = _compress_grep(input_lines)
        assert not any(b"ctx after skip" in l for l in result)


# ---------------------------------------------------------------------------
# 3. cat compressor — strips comments/blanks, truncates at 500 lines
# ---------------------------------------------------------------------------

class TestCatCompressor:
    def test_strips_blank_lines(self):
        input_lines = lines("code here", "", "  ", "more code")
        result = _compress_cat(input_lines)
        assert all(l.strip() for l in result)

    def test_strips_hash_comments(self):
        input_lines = lines("# comment", "code", "# another comment")
        result = _compress_cat(input_lines)
        assert all(not l.strip().startswith(b"#") for l in result)
        assert b"code\n" in result

    def test_strips_double_slash_comments(self):
        input_lines = lines("// comment", "code line")
        result = _compress_cat(input_lines)
        assert not any(b"//" in l for l in result)

    def test_strips_dash_dash_comments(self):
        input_lines = lines("-- sql comment", "SELECT 1")
        result = _compress_cat(input_lines)
        assert not any(l.strip().startswith(b"--") for l in result)

    def test_strips_star_comments(self):
        input_lines = lines("* javadoc line", "actual code")
        result = _compress_cat(input_lines)
        assert not any(l.strip().startswith(b"*") for l in result)

    def test_truncates_at_500(self):
        input_lines = lines(*[f"line {i}" for i in range(600)])
        result = _compress_cat(input_lines)
        assert len(result) == CAT_TRUNCATE_LINES

    def test_keeps_code_lines(self):
        code = ["def foo():", "    return 42", "class Bar:"]
        input_lines = lines(*code)
        result = _compress_cat(input_lines)
        assert len(result) == len(code)

    def test_empty_input(self):
        assert _compress_cat([]) == []

    def test_exactly_100_lines_kept(self):
        input_lines = lines(*[f"x = {i}" for i in range(100)])
        result = _compress_cat(input_lines)
        assert len(result) == 100


# ---------------------------------------------------------------------------
# 4. git diff compressor — strips metadata lines
# ---------------------------------------------------------------------------

class TestGitDiffCompressor:
    def _diff_lines(self):
        return [
            b"diff --git a/foo.py b/foo.py\n",
            b"index abc123..def456 100644\n",
            b"--- a/foo.py\n",
            b"+++ b/foo.py\n",
            b"@@ -1,3 +1,4 @@\n",
            b" context\n",
            b"-removed\n",
            b"+added\n",
        ]

    def test_strips_index_metadata(self):
        result = _compress_git_diff(self._diff_lines())
        assert not any(l.startswith(b"index ") for l in result)

    def test_keeps_diff_header(self):
        result = _compress_git_diff(self._diff_lines())
        assert any(l.startswith(b"diff --git") for l in result)

    def test_keeps_hunk_header(self):
        result = _compress_git_diff(self._diff_lines())
        assert any(l.startswith(b"@@") for l in result)

    def test_keeps_added_removed_lines(self):
        result = _compress_git_diff(self._diff_lines())
        assert any(l.startswith(b"+added") for l in result)
        assert any(l.startswith(b"-removed") for l in result)

    def test_keeps_file_headers(self):
        result = _compress_git_diff(self._diff_lines())
        assert any(l.startswith(b"---") for l in result)
        assert any(l.startswith(b"+++") for l in result)

    def test_empty_input(self):
        assert _compress_git_diff([]) == []

    def test_dispatch_from_compress_git(self):
        """_compress_git dispatches to diff compressor when diff headers present."""
        input_lines = self._diff_lines()
        result = _compress_git(input_lines)
        assert not any(l.startswith(b"index ") for l in result)

    def test_no_stripping_without_dotdot(self):
        """'index' lines without '..' should NOT be stripped."""
        input_lines = [
            b"diff --git a/x b/x\n",
            b"index on main: abc123\n",  # no '..' — should be kept
            b"+some change\n",
        ]
        result = _compress_git_diff(input_lines)
        assert any(b"index on main" in l for l in result)


# ---------------------------------------------------------------------------
# 5. git log compressor — caps at 20 entries
# ---------------------------------------------------------------------------

class TestGitLogCompressor:
    def _make_standard_log(self, n):
        lines_out = []
        for i in range(n):
            sha = f"{'a' * 40}"
            lines_out.append(f"commit {sha}\n".encode())
            lines_out.append(b"Author: Test <t@t.com>\n")
            lines_out.append(b"Date:   Mon Jan 1 00:00:00 2024\n")
            lines_out.append(b"\n")
            lines_out.append(f"    Message {i}\n".encode())
            lines_out.append(b"\n")
        return lines_out

    def _make_oneline_log(self, n):
        return [f"{'a' * 7} Message {i}\n".encode() for i in range(n)]

    def test_standard_log_capped_at_20(self):
        input_lines = self._make_standard_log(25)
        result = _compress_git_log(input_lines)
        commit_count = sum(1 for l in result if l.startswith(b"commit "))
        assert commit_count == MAX_LOG_ENTRIES

    def test_standard_log_under_limit_unchanged(self):
        input_lines = self._make_standard_log(10)
        result = _compress_git_log(input_lines)
        commit_count = sum(1 for l in result if l.startswith(b"commit "))
        assert commit_count == 10

    def test_oneline_log_capped_at_20(self):
        input_lines = self._make_oneline_log(30)
        result = _compress_git_log(input_lines)
        assert len(result) == MAX_LOG_ENTRIES

    def test_oneline_log_under_limit_unchanged(self):
        input_lines = self._make_oneline_log(5)
        result = _compress_git_log(input_lines)
        assert len(result) == 5

    def test_dispatch_from_compress_git(self):
        """_compress_git dispatches to log compressor for standard log format."""
        input_lines = self._make_standard_log(25)
        result = _compress_git(input_lines)
        commit_count = sum(1 for l in result if l.startswith(b"commit "))
        assert commit_count == MAX_LOG_ENTRIES

    def test_dispatch_oneline_from_compress_git(self):
        input_lines = self._make_oneline_log(30)
        result = _compress_git(input_lines)
        assert len(result) == MAX_LOG_ENTRIES

    def test_empty_input(self):
        assert _compress_git_log([]) == []


# ---------------------------------------------------------------------------
# 6. generic fallback — collapses repeated lines, truncates at 200
# ---------------------------------------------------------------------------

class TestGenericCompressor:
    def test_collapses_repeated_lines(self):
        repeated = [b"same line\n"] * (GENERIC_REPEAT_THRESHOLD + 2)
        result = _compress_generic(repeated)
        match_lines = [l for l in result if l == b"same line\n"]
        assert len(match_lines) == 1
        assert any(b"repeated" in l for l in result)

    def test_does_not_collapse_short_runs(self):
        """Runs of exactly GENERIC_REPEAT_THRESHOLD should NOT be collapsed."""
        run = [b"line\n"] * GENERIC_REPEAT_THRESHOLD
        result = _compress_generic(run)
        assert result == run

    def test_truncates_at_200(self):
        input_lines = [f"line {i}\n".encode() for i in range(300)]
        result = _compress_generic(input_lines)
        assert len(result) == GENERIC_TRUNCATE_LINES + 1  # +1 for truncation note
        assert b"truncated" in result[-1]

    def test_truncation_note_correct_count(self):
        input_lines = [f"line {i}\n".encode() for i in range(250)]
        result = _compress_generic(input_lines)
        note = result[-1]
        truncated_n = 250 - GENERIC_TRUNCATE_LINES
        assert str(truncated_n).encode() in note

    def test_no_truncation_under_limit(self):
        input_lines = [f"line {i}\n".encode() for i in range(100)]
        result = _compress_generic(input_lines)
        assert len(result) == 100

    def test_empty_input(self):
        assert _compress_generic([]) == []

    def test_non_repeated_lines_kept(self):
        input_lines = [f"unique {i}\n".encode() for i in range(10)]
        result = _compress_generic(input_lines)
        assert result == input_lines

    def test_repeated_note_contains_run_count(self):
        n = GENERIC_REPEAT_THRESHOLD + 3
        repeated = [b"x\n"] * n
        result = _compress_generic(repeated)
        assert str(n).encode() in b"".join(result)


# ---------------------------------------------------------------------------
# 7. --full flag — bypasses compression, raw output
# ---------------------------------------------------------------------------

class TestFullFlag:
    def test_full_bypasses_compression(self):
        """With --full, output should not have a wumw header."""
        env = {"WUMW_SESSION": "test-full"}
        # Generate many identical lines to trigger generic compression
        payload = b"same line\n" * 50
        r = run_wumw("--full", "cat", "/dev/stdin", env=env, input_=payload)
        assert b"wumw" not in r.stdout
        assert r.stdout == payload

    def test_full_preserves_exit_code(self):
        env = {"WUMW_SESSION": "test-full-exit"}
        r = run_wumw("--full", "sh", "-c", "exit 7", env=env)
        assert r.returncode == 7

    def test_full_preserves_stderr(self):
        env = {"WUMW_SESSION": "test-full-stderr"}
        r = run_wumw("--full", "sh", "-c", "printf err >&2", env=env)
        assert b"err" in r.stderr

    def test_full_logs_full_field(self, tmp_path):
        """--full mode should write full=true into the JSONL log."""
        session_id = "test-full-log-field"
        log_file = LOG_DIR / f"{session_id}.jsonl"
        if log_file.exists():
            log_file.unlink()

        env = {"WUMW_SESSION": session_id}
        run_wumw("--full", "true", env=env)

        assert log_file.exists()
        entry = json.loads(log_file.read_text().splitlines()[-1])
        assert entry.get("full") is True


# ---------------------------------------------------------------------------
# 8. Compression header — appears only when lines are reduced
# ---------------------------------------------------------------------------

class TestCompressionHeader:
    def test_header_present_when_lines_reduced(self):
        """When compression reduces line count, a '# wumw: N → M lines' header appears."""
        env = {"WUMW_SESSION": "test-header-reduced"}
        # cat compressor strips comment and blank lines — use a file full of them
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            # 50 comment lines + 1 code line → should reduce
            f.write(b"# comment\n" * 50 + b"code = 1\n")
            tmpname = f.name
        try:
            r = run_wumw("cat", tmpname, env=env)
            assert b"# wumw:" in r.stdout
        finally:
            os.unlink(tmpname)

    def test_header_absent_when_no_reduction(self):
        """When output is not reduced, the header should not appear."""
        env = {"WUMW_SESSION": "test-header-none"}
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"unique line one\nunique line two\nunique line three\n")
            tmpname = f.name
        try:
            r = run_wumw("cat", tmpname, env=env)
            assert b"# wumw:" not in r.stdout
        finally:
            os.unlink(tmpname)

    def test_header_format(self):
        """Header must match '# wumw: N → M lines'."""
        env = {"WUMW_SESSION": "test-header-format"}
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"# comment\n" * 20 + b"\n" * 20 + b"code\n")
            tmpname = f.name
        try:
            r = run_wumw("cat", tmpname, env=env)
            assert b"# wumw:" in r.stdout
            header_line = next(l for l in r.stdout.splitlines() if b"# wumw:" in l)
            assert b"\xe2\x86\x92" in header_line or b"->" in header_line or "→".encode() in header_line
        finally:
            os.unlink(tmpname)


# ---------------------------------------------------------------------------
# 9. JSONL logging — correct fields written to .wumw/sessions/
# ---------------------------------------------------------------------------

class TestJsonlLogging:
    def _run_and_read_log(self, *args, extra_env=None):
        session_id = f"test-log-{os.getpid()}-{id(args)}"
        log_file = LOG_DIR / f"{session_id}.jsonl"
        if log_file.exists():
            log_file.unlink()

        env = {"WUMW_SESSION": session_id, **(extra_env or {})}
        run_wumw(*args, env=env)

        assert log_file.exists(), f"Log file not created: {log_file}"
        lines_raw = log_file.read_text().splitlines()
        return [json.loads(l) for l in lines_raw if l.strip()]

    def test_creates_log_file(self):
        entries = self._run_and_read_log("true")
        assert len(entries) >= 1

    def test_log_has_timestamp(self):
        entries = self._run_and_read_log("true")
        assert "timestamp" in entries[-1]

    def test_log_has_session_id(self):
        session_id = f"test-log-session-{os.getpid()}"
        log_file = LOG_DIR / f"{session_id}.jsonl"
        if log_file.exists():
            log_file.unlink()
        env = {"WUMW_SESSION": session_id}
        run_wumw("true", env=env)
        entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
        assert entries[-1]["session_id"] == session_id

    def test_log_has_command(self):
        entries = self._run_and_read_log("true")
        assert entries[-1]["command"] == "true"

    def test_log_has_exit_code(self):
        entries = self._run_and_read_log("true")
        assert entries[-1]["exit_code"] == 0

    def test_log_exit_code_nonzero(self):
        entries = self._run_and_read_log("sh", "-c", "exit 3")
        assert entries[-1]["exit_code"] == 3

    def test_log_has_stdout_bytes(self):
        entries = self._run_and_read_log("sh", "-c", "printf hello")
        assert "stdout_bytes" in entries[-1]
        assert entries[-1]["stdout_bytes"] == 5

    def test_log_has_stdout_lines(self):
        entries = self._run_and_read_log("sh", "-c", "printf 'a\\nb\\nc\\n'")
        assert entries[-1]["stdout_lines"] == 3

    def test_log_has_stderr_bytes(self):
        entries = self._run_and_read_log("sh", "-c", "printf err >&2")
        assert "stderr_bytes" in entries[-1]
        assert entries[-1]["stderr_bytes"] == 3

    def test_log_compressed_lines_present_when_reduced(self):
        """compressed_lines field should appear when compression reduced output."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            # Many hash comments — cat compressor will strip them all
            f.write(b"# comment\n" * 50 + b"code\n")
            tmpname = f.name
        try:
            entries = self._run_and_read_log("cat", tmpname)
            assert "compressed_lines" in entries[-1]
        finally:
            os.unlink(tmpname)

    def test_log_compressed_lines_absent_when_no_reduction(self):
        """compressed_lines field should NOT appear when no lines were dropped."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"a = 1\nb = 2\nc = 3\n")
            tmpname = f.name
        try:
            entries = self._run_and_read_log("cat", tmpname)
            assert "compressed_lines" not in entries[-1]
        finally:
            os.unlink(tmpname)

    def test_log_appends_multiple_entries(self):
        session_id = f"test-log-append-{os.getpid()}"
        log_file = LOG_DIR / f"{session_id}.jsonl"
        if log_file.exists():
            log_file.unlink()
        env = {"WUMW_SESSION": session_id}
        run_wumw("true", env=env)
        run_wumw("true", env=env)
        entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
        assert len(entries) == 2

    def test_log_full_field_absent_without_flag(self):
        entries = self._run_and_read_log("true")
        assert "full" not in entries[-1]


# ---------------------------------------------------------------------------
# 10. compress() public API — dispatch and return values
# ---------------------------------------------------------------------------

class TestCompressAPI:
    def test_returns_tuple_of_three(self):
        result = compress("cat", b"code\n")
        assert isinstance(result, tuple) and len(result) == 3

    def test_unknown_command_uses_generic(self):
        """Unregistered command should fall back to generic compressor."""
        # Many identical lines → generic collapses them
        data = b"same\n" * (GENERIC_REPEAT_THRESHOLD + 2)
        compressed, orig, comp = compress("unknown_cmd_xyz", data)
        assert comp < orig

    def test_cat_dispatches_correctly(self):
        data = b"# comment\n" * 10 + b"code\n"
        compressed, orig, comp = compress("cat", data)
        assert comp < orig

    def test_rg_dispatches_correctly(self):
        data = b"file.py:1:match\n" * (MAX_MATCHES_PER_FILE + 3)
        compressed, orig, comp = compress("rg", data)
        assert comp < orig

    def test_grep_dispatches_correctly(self):
        data = b"file.py:1:match\n" * (MAX_MATCHES_PER_FILE + 3)
        compressed, orig, comp = compress("grep", data)
        assert comp < orig

    def test_git_dispatches_diff(self):
        data = b"diff --git a/x b/x\nindex abc..def 100644\n+added\n"
        compressed, orig, comp = compress("git", data)
        assert b"index abc..def" not in compressed

    def test_original_line_count_correct(self):
        data = b"a\nb\nc\n"
        _, orig, _ = compress("cat", data)
        assert orig == 3

    def test_empty_input(self):
        compressed, orig, comp = compress("cat", b"")
        assert compressed == b""
        assert orig == 0
        assert comp == 0
