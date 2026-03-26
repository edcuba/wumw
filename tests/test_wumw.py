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

import wumw.savings as savings
import wumw.state as state
from wumw.compress import (
    compress,
    _compress_cat,
    _compress_generic,
    _compress_grep,
    _compress_git,
    _compress_git_diff,
    _compress_git_log,
    _compress_listing,
    MAX_MATCHES_PER_FILE,
    MAX_CONTEXT_LINES,
    CAT_OUTLINE_THRESHOLD,
    CAT_TRUNCATE_LINES,
    GIT_DIFF_CONTEXT_LINES,
    GIT_DIFF_REVIEW_MIN_HUNK_LINES,
    LISTING_MAX_ENTRIES,
    MAX_LOG_ENTRIES,
    GENERIC_TRUNCATE_LINES,
    GENERIC_REPEAT_THRESHOLD,
    is_probably_binary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def lines(*strs):
    """Return list[bytes] with newlines, as splitlines(keepends=True) would."""
    return [s.encode() + b"\n" for s in strs]


def join(line_list):
    return b"".join(line_list)


REPO_ROOT = Path(__file__).parent.parent
WUMW_CMD = [str(Path(sys.executable).with_name("wumw"))]
BINARY_STDOUT_CMD = [
    sys.executable,
    "-c",
    "import sys; sys.stdout.buffer.write(b'\\x00\\xffsame line\\n\\x01tail')",
]


def log_dir():
    return Path(os.environ["WUMW_HOME"]) / "sessions"


def run_wumw(*args, env=None, input_=None, cwd=None):
    """Run the wumw CLI, return CompletedProcess."""
    cmd = WUMW_CMD + list(args)
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        capture_output=True,
        cwd=cwd or REPO_ROOT,
        env=merged_env,
        input=input_,
    )


@pytest.fixture(autouse=True)
def isolated_wumw_home(monkeypatch, tmp_path):
    monkeypatch.setenv("WUMW_HOME", str(tmp_path / ".wumw"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


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

    def test_module_entrypoint_runs_main(self, tmp_path):
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src"), "WUMW_HOME": str(tmp_path / ".wumw")}
        r = subprocess.run(
            [sys.executable, "-m", "wumw.cli", "--full", "sh", "-c", "printf ok"],
            capture_output=True,
            cwd=REPO_ROOT,
            env=env,
        )
        assert r.returncode == 0
        assert r.stdout == b"ok"

    def test_binary_stdout_skips_compression(self):
        env = {"WUMW_SESSION": "test-binary-stdout"}
        r = run_wumw(*BINARY_STDOUT_CMD, env=env)
        assert r.returncode == 0
        assert r.stdout == b"\x00\xffsame line\n\x01tail"
        assert b"# wumw:" not in r.stdout


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

    def test_adds_omission_hint_when_cap_drops_matches(self):
        input_lines = [
            self._make_match("file.py", i, f"match {i}")
            for i in range(MAX_MATCHES_PER_FILE + 2)
        ]
        result = _compress_grep(input_lines)
        assert b"# wumw: file.py kept 5/7 matches; 2 more matches omitted (2 over cap at lines 5, 6)\n" in result

    def test_adds_omission_hint_when_duplicates_dropped(self):
        input_lines = [
            self._make_match("a.py", 1, "duplicate content"),
            self._make_match("a.py", 2, "duplicate content"),
            self._make_match("a.py", 3, "duplicate content"),
        ]
        result = _compress_grep(input_lines)
        assert (
            b"# wumw: a.py kept 1/3 matches; 2 more matches omitted (2 duplicate)\n"
            in result
        )

    def test_omission_hint_not_added_when_nothing_dropped(self):
        input_lines = [
            self._make_match("a.py", 1, "hit"),
            self._make_match("a.py", 2, "other hit"),
        ]
        result = _compress_grep(input_lines)
        assert not any(line.startswith(b"# wumw: a.py kept") for line in result)

    def test_respects_env_cap_override(self, monkeypatch):
        monkeypatch.setenv("WUMW_RG_CAP", "2")
        input_lines = [
            self._make_match("file.py", i, f"match {i}")
            for i in range(4)
        ]
        result = _compress_grep(input_lines)

        count = sum(1 for l in result if l.startswith(b"file.py:"))
        assert count == 2
        assert b"# wumw: file.py kept 2/4 matches; 2 more matches omitted (2 over cap at lines 2, 3)\n" in result


# ---------------------------------------------------------------------------
# 2b. rg/grep compressor — real-world fixture tests
# ---------------------------------------------------------------------------

class TestGrepCompressorRealWorld:
    """
    Tests built from actual rg/grep output captured against the django codebase.
    These catch edge cases that synthetic _make_match helpers miss.
    """

    def _lines(self, *rows):
        return [row.encode() + b"\n" for row in rows]

    def test_multifile_cap_omission_shows_line_numbers(self):
        # Real pattern: `rg "get_placeholder_sql" --no-heading -n` across two files.
        # compiler.py has 11 hits; fields/__init__.py has 8. Cap=5 per file.
        # Omitted compiler.py lines: 1782, 1786, 1787, 1788, 2088, 2090
        # Omitted fields/__init__.py lines: 201, 202, 208
        compiler_lines = self._lines(
            "django/db/models/sql/compiler.py:1693:    def field_as_sql(self, field, get_placeholder_sql, val):",
            "django/db/models/sql/compiler.py:1697:        fields with get_placeholder_sql(), and compilable defined",
            "django/db/models/sql/compiler.py:1706:        elif get_placeholder_sql is not None:",
            "django/db/models/sql/compiler.py:1709:            sql, params = get_placeholder_sql(val, self, self.connection)",
            "django/db/models/sql/compiler.py:1781:        get_placeholder_sqls = [",
            # over cap below
            "django/db/models/sql/compiler.py:1782:            getattr(field, 'get_placeholder_sql', None) for field in fields",
            "django/db/models/sql/compiler.py:1786:                self.field_as_sql(field, get_placeholder_sql, value)",
            "django/db/models/sql/compiler.py:1787:                for field, get_placeholder_sql, value in zip(",
            "django/db/models/sql/compiler.py:1788:                    fields, get_placeholder_sqls, row",
            "django/db/models/sql/compiler.py:2088:                get_placeholder_sql := getattr(field, 'get_placeholder_sql', None)",
            "django/db/models/sql/compiler.py:2090:                sql, params = get_placeholder_sql(val, self, self.connection)",
        )
        fields_lines = self._lines(
            "django/db/models/fields/__init__.py:188:        # Allow for both `get_placeholder` and `get_placeholder_sql`",
            "django/db/models/fields/__init__.py:191:            get_placeholder := cls.__dict__.get('get_placeholder')",
            "django/db/models/fields/__init__.py:192:        ) is not None and 'get_placeholder_sql' not in cls.__dict__:",
            "django/db/models/fields/__init__.py:194:                'Field.get_placeholder is deprecated in favor of get_placeholder_sql.'",
            "django/db/models/fields/__init__.py:195:                f'Define {cls.__module__}.{cls.__qualname__}.get_placeholder_sql '",
            # over cap below
            "django/db/models/fields/__init__.py:201:            def get_placeholder_sql(self, value, compiler, connection):",
            "django/db/models/fields/__init__.py:202:                placeholder = get_placeholder(self, value, compiler, connection)",
            "django/db/models/fields/__init__.py:208:            setattr(cls, 'get_placeholder_sql', get_placeholder_sql)",
        )
        result = _compress_grep(compiler_lines + fields_lines)
        output = b"".join(result)

        # compiler.py: 5 kept, 6 omitted.
        # Line 2090 has identical content to line 1709 → counted as duplicate, not cap.
        # Cap-omitted: 1782, 1786, 1787, 1788, 2088 (5). Duplicate: 2090 (1).
        assert b"compiler.py kept 5/11 matches" in output
        assert b"1 duplicate" in output
        assert b"5 over cap at lines 1782, 1786, 1787, 1788, 2088" in output
        # fields/__init__.py: 5 kept, 3 omitted at lines 201, 202, 208
        assert b"__init__.py kept 5/8 matches" in output
        assert b"3 over cap at lines 201, 202, 208" in output
        # 10 match lines kept total (5 per file)
        kept = [l for l in result if b"get_placeholder" in l and not l.startswith(b"#")]
        assert len(kept) == 10

    def test_context_around_skipped_match_is_suppressed(self):
        # Context lines (both pre- and post-) around a skipped match must not
        # appear in output. The separator and its following context are buffered
        # and only flushed if the next match in the group is kept.
        lines = self._lines(
            # 5 matches to saturate cap (cap=5)
            "compiler.py:1693:match 1",
            "compiler.py:1697:match 2",
            "compiler.py:1706:match 3",
            "compiler.py:1709:match 4",
            "compiler.py:1781:match 5",
            "--",
            "compiler.py:1785-        pre-ctx of over-cap match",
            "compiler.py:1786:match 6 over cap",
            "compiler.py:1787-        post-ctx of over-cap match",
        )
        result = _compress_grep(lines)
        output = b"".join(result)
        # Pre- and post-ctx of the skipped match must be suppressed
        assert b"compiler.py:1785-" not in output
        assert b"compiler.py:1787-" not in output
        # Separator must also be suppressed (no kept match follows it)
        assert b"--\n" not in output
        # Skipped match itself must not appear
        assert b"compiler.py:1786:" not in output

    def test_post_context_of_skipped_match_flushed_as_pre_context_when_next_is_kept(self):
        # Buffered post-ctx of a skipped match becomes pre-ctx of the next kept match.
        # Two files: file A fills the cap and has a skipped match with post-ctx;
        # then file B has a kept match — the buffer is discarded when file changes.
        # Instead, use one file where a skipped match is followed by a kept match
        # in a second file (different filepath resets per-file state).
        lines = self._lines(
            # Fill cap for a.py
            "a.py:1:match 1",
            "a.py:2:match 2",
            "a.py:3:match 3",
            "a.py:4:match 4",
            "a.py:5:match 5",
            "--",
            "a.py:9-pre-ctx of skipped",
            "a.py:10:match 6 over cap for a.py",  # skipped
            "a.py:11-post-ctx buffered",           # buffered
            "--",
            # b.py has a kept match; buffered a.py ctx should be discarded (separator resets)
            "b.py:1:match in b.py",
        )
        result = _compress_grep(lines)
        output = b"".join(result)
        # Buffered post-ctx (a.py:11-) must not appear: separator resets the buffer
        assert b"a.py:11-" not in output
        # b.py match is kept
        assert b"b.py:1:" in output

    def test_group_separator_passthrough_between_kept_matches(self):
        # `--` between two kept matches must be preserved in output.
        lines = self._lines(
            "a.py:1:first match",
            "--",
            "a.py:10:second match",
        )
        result = _compress_grep(lines)
        assert b"--\n" in result

    def test_single_file_grep_no_path_prefix_passes_through(self):
        # grep without -H or rg with single file: output is `lineno:content` only,
        # no file path. The regex won't match → lines pass through unchanged.
        lines = self._lines(
            "152:    def test_get_placeholder_deprecation(self):",
            "167:    def test_get_placeholder_sql_shim(self):",
        )
        result = _compress_grep(lines)
        assert result == lines  # no compression, no omission hints

    def test_dedup_within_file_not_across_files(self):
        # The same match content in two different files must NOT be deduplicated —
        # dedup is per-file only.
        shared_sig = "    def get_placeholder_sql(self, value, compiler, connection):"
        lines = self._lines(
            f"array.py:127:{shared_sig}",
            f"ranges.py:87:{shared_sig}",
        )
        result = _compress_grep(lines)
        output = b"".join(result)
        # Both lines must survive
        assert b"array.py:127:" in output
        assert b"ranges.py:87:" in output
        assert b"omitted" not in output

    def test_line_number_hint_truncated_at_ten(self):
        # When more than 10 matches are capped, hint shows first 10 line numbers + count.
        lines = [
            f"big.py:{i}:match content {i}".encode() + b"\n"
            for i in range(1, MAX_MATCHES_PER_FILE + 16)  # 5 kept, 15 capped
        ]
        result = _compress_grep(lines)
        output = b"".join(result)
        # Should show 10 line numbers then (+5 more)
        assert b"(+5 more)" in output
        assert b"over cap at lines" in output

    def test_binary_match_notice_passes_through(self):
        # rg emits lines like "Binary file foo.bin matches" for binary hits.
        # These don't match the regex and must pass through.
        lines = self._lines(
            "Binary file django/db/models/sql/compiler.pyc matches",
            "a.py:1:normal match",
        )
        result = _compress_grep(lines)
        output = b"".join(result)
        assert b"Binary file" in output
        assert b"a.py:1:" in output


# ---------------------------------------------------------------------------
# 3. cat compressor — strips comments/blanks, truncates at 500 lines
# ---------------------------------------------------------------------------

class TestCatCompressor:
    def test_strips_blank_lines(self):
        input_lines = lines("code here", "", "  ", "more code")
        result = _compress_cat(input_lines)
        assert all(l.strip() for l in result)

    def test_strips_hash_comments_in_python_files(self):
        # Python files: inline # comments are stripped.
        input_lines = lines("# comment", "code", "# another comment")
        result = _compress_cat(input_lines, filename="module.py")
        assert all(not l.strip().startswith(b"#") for l in result)
        assert b"code\n" in result

    def test_preserves_hash_in_non_python_files(self):
        # Shell scripts and other formats use # for meaningful content.
        input_lines = lines("#!/usr/bin/env bash", "echo hello", "# shellcheck disable=SC2086")
        result = _compress_cat(input_lines, filename="run.sh")
        assert b"#!/usr/bin/env bash\n" in result
        assert b"# shellcheck disable=SC2086\n" in result

    def test_preserves_double_slash_comments(self):
        # // is meaningful in JS/TS/C — must not be stripped.
        input_lines = lines("// @ts-ignore", "const x = 1;")
        result = _compress_cat(input_lines, filename="index.ts")
        assert any(b"//" in l for l in result)

    def test_preserves_dash_dash_comments(self):
        # -- is a SQL comment — must not be stripped from .sql files.
        input_lines = lines("-- drop table users;", "SELECT 1")
        result = _compress_cat(input_lines, filename="migration.sql")
        assert any(l.strip().startswith(b"--") for l in result)

    def test_preserves_star_lines(self):
        # * is used in Markdown bullets and JSDoc — must not be stripped.
        input_lines = lines("* bullet point", "* @param foo", "actual code")
        result = _compress_cat(input_lines, filename="README.md")
        assert any(l.strip().startswith(b"*") for l in result)

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

    def test_python_outline_includes_decorators_and_doc_hint(self):
        input_lines = lines(
            "class Example:",
            "    @classmethod",
            "    def build(cls):",
            '        """Build an instance from repo-local defaults."""',
            "        return cls()",
        )
        result = _compress_cat(
            input_lines,
            filename="example.py",
            total_lines=CAT_OUTLINE_THRESHOLD + 1,
        )
        joined = join(result)

        assert b"# wumw: Python outline for example.py" in joined
        assert b"L2:     @classmethod\n" in joined
        assert b"L3:     def build(cls):  # doc: Build an instance from repo-local defaults.\n" in joined

    def test_python_outline_uses_first_body_line_as_hint_when_no_docstring(self):
        input_lines = lines(
            "async def fetch_data(client, key):",
            "    if key in client.cache:",
            "        return client.cache[key]",
        )
        result = _compress_cat(
            input_lines,
            filename="client.py",
            total_lines=CAT_OUTLINE_THRESHOLD + 1,
        )
        joined = join(result)

        assert b"L1: async def fetch_data(client, key):  # if key in client.cache:\n" in joined

    def test_respects_env_line_override_in_hint(self, monkeypatch):
        monkeypatch.setenv("WUMW_CAT_LINES", "3")
        input_lines = lines("line 1", "line 2", "line 3", "line 4", "line 5")
        result = _compress_cat(input_lines, filename="notes.txt", total_lines=5)

        assert result[:3] == lines("line 1", "line 2", "line 3")
        assert b"head -3\n" in result[-1]


# ---------------------------------------------------------------------------
# 4. git diff compressor — preserves structure, shrinks oversized hunks
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

    def test_small_hunk_kept_in_full(self):
        input_lines = [
            b"diff --git a/foo.py b/foo.py\n",
            b"index abc123..def456 100644\n",
            b"--- a/foo.py\n",
            b"+++ b/foo.py\n",
            b"@@ -1,4 +1,4 @@\n",
            b" context 1\n",
            b"-removed\n",
            b"+added\n",
            b" context 2\n",
        ]

        result = _compress_git_diff(input_lines)

        assert b"... (" not in join(result)
        assert join(result).count(b" context ") == 2

    def test_large_hunk_omits_middle_context(self):
        before = [f" context before {i}".encode() + b"\n" for i in range(12)]
        after = [f" context after {i}".encode() + b"\n" for i in range(12)]
        input_lines = [
            b"diff --git a/foo.py b/foo.py\n",
            b"index abc123..def456 100644\n",
            b"--- a/foo.py\n",
            b"+++ b/foo.py\n",
            b"@@ -1,28 +1,28 @@\n",
            *before,
            b"-removed\n",
            b"+added\n",
            *after,
        ]

        result = _compress_git_diff(input_lines)
        joined = join(result)

        assert b"@@ -1,28 +1,28 @@" in joined
        assert b"... (" in joined
        assert b"context before 0\n" not in joined
        assert b"context before 11\n" in joined
        assert b"context after 0\n" in joined
        assert b"context after 11\n" not in joined
        assert b"-removed\n" in joined
        assert b"+added\n" in joined

    def test_large_hunk_keeps_context_near_each_change_cluster(self):
        shared = [f" shared {i}".encode() + b"\n" for i in range(20)]
        input_lines = [
            b"diff --git a/foo.py b/foo.py\n",
            b"--- a/foo.py\n",
            b"+++ b/foo.py\n",
            b"@@ -1,24 +1,24 @@\n",
            b"-first change\n",
            b"+first replacement\n",
            *shared,
            b"-second change\n",
            b"+second replacement\n",
        ]

        result = _compress_git_diff(input_lines)
        joined = join(result)

        assert joined.count(b"... (") == 1
        assert f" shared {GIT_DIFF_CONTEXT_LINES - 1}\n".encode() in joined
        assert f" shared {GIT_DIFF_CONTEXT_LINES}\n".encode() not in joined
        tail_index = len(shared) - GIT_DIFF_CONTEXT_LINES
        assert f" shared {tail_index}\n".encode() in joined
        assert f" shared {tail_index - 1}\n".encode() not in joined

    def test_hunk_below_review_threshold_not_compressed(self):
        context_count = max(0, GIT_DIFF_REVIEW_MIN_HUNK_LINES - 2)
        input_lines = [
            b"diff --git a/foo.py b/foo.py\n",
            b"--- a/foo.py\n",
            b"+++ b/foo.py\n",
            b"@@ -1,20 +1,20 @@\n",
            *[f" context {i}".encode() + b"\n" for i in range(context_count)],
            b"-removed\n",
            b"+added\n",
        ]

        result = _compress_git_diff(input_lines)

        assert b"... (" not in join(result)

    def test_keeps_hunk_content_that_looks_like_file_headers(self):
        input_lines = [
            b"diff --git a/foo.py b/foo.py\n",
            b"--- a/foo.py\n",
            b"+++ b/foo.py\n",
            b"@@ -1,3 +1,3 @@\n",
            b" context\n",
            b"--- removed text that starts like a header\n",
            b"+++ added text that starts like a header\n",
        ]

        result = _compress_git_diff(input_lines)
        joined = join(result)

        assert b"--- removed text that starts like a header\n" in joined
        assert b"+++ added text that starts like a header\n" in joined

    def _multifile_diff(self, n):
        """Build a synthetic diff with n files, each with one small hunk."""
        lines = []
        for i in range(n):
            lines += [
                f"diff --git a/file{i}.py b/file{i}.py\n".encode(),
                f"index abc{i:03d}..def{i:03d} 100644\n".encode(),
                f"--- a/file{i}.py\n".encode(),
                f"+++ b/file{i}.py\n".encode(),
                f"@@ -1,2 +1,2 @@\n".encode(),
                b" context\n",
                f"-removed {i}\n".encode(),
                f"+added {i}\n".encode(),
            ]
        return lines

    def test_single_file_keeps_full_headers(self):
        """With only 1 file, full headers are kept (below threshold)."""
        result = _compress_git_diff(self._multifile_diff(1))
        joined = join(result)
        assert b"diff --git" in joined
        assert b"--- a/file0.py" in joined
        assert b"+++ b/file0.py" in joined

    def test_multifile_summary_header_added(self):
        """With >3 files, a summary line is prepended."""
        result = _compress_git_diff(self._multifile_diff(4))
        joined = join(result)
        assert b"# wumw: 4 files changed" in joined
        assert b"file headers compressed" in joined

    def test_multifile_file_lines_collapsed_to_compact(self):
        """With >3 files, ---/+++ lines are dropped; diff --git line is kept."""
        result = _compress_git_diff(self._multifile_diff(4))
        joined = join(result)
        assert b"diff --git a/file0.py b/file0.py" in joined
        assert b"diff --git a/file3.py b/file3.py" in joined
        # ---/+++ lines are redundant when file is identified by diff --git header
        assert b"--- a/file0.py" not in joined
        assert b"+++ b/file0.py" not in joined

    def test_multifile_hunk_content_preserved(self):
        """Hunk content is kept even when file headers are compressed."""
        result = _compress_git_diff(self._multifile_diff(4))
        joined = join(result)
        assert b"-removed 0\n" in joined
        assert b"+added 3\n" in joined
        assert b"@@ -1,2 +1,2 @@" in joined

    def test_multifile_index_lines_stripped(self):
        """Index lines with '..' are stripped regardless of file count."""
        result = _compress_git_diff(self._multifile_diff(4))
        assert not any(l.startswith(b"index ") for l in result)

    def test_exactly_threshold_no_compression(self):
        """With exactly 3 files (= threshold, not >), headers are not compressed."""
        result = _compress_git_diff(self._multifile_diff(3))
        joined = join(result)
        assert b"diff --git" in joined
        assert b"--- a/file0.py" in joined


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

    def test_respects_env_entry_override(self, monkeypatch):
        monkeypatch.setenv("WUMW_GIT_LOG_ENTRIES", "3")
        input_lines = self._make_oneline_log(8)
        result = _compress_git_log(input_lines)

        assert len(result) == 3


# ---------------------------------------------------------------------------
# 6. fd/find/ls compressor — deduplicates and samples by extension
# ---------------------------------------------------------------------------

class TestListingCompressor:
    def test_deduplicates_repeated_entries_under_cap(self):
        input_lines = lines("src/app.py", "src/app.py", "README.md")
        result = _compress_listing(input_lines)

        assert result == lines("src/app.py", "README.md")

    def test_groups_large_listing_by_extension_and_caps_entries(self):
        input_lines = []
        for i in range(LISTING_MAX_ENTRIES):
            input_lines.append(f"src/module_{i}.py".encode() + b"\n")
        for i in range(12):
            input_lines.append(f"docs/page_{i}.md".encode() + b"\n")
        for i in range(8):
            input_lines.append(f"bin/tool_{i}".encode() + b"\n")
        result = _compress_listing(input_lines)
        joined = join(result)

        sampled_entries = [line for line in result if not line.startswith(b"#")]
        assert len(sampled_entries) <= LISTING_MAX_ENTRIES
        assert b"# wumw: kept " in joined
        assert b"# .py: " in joined
        assert b"# .md: " in joined
        assert b"# [no extension]: " in joined
        assert b"more .py entries omitted" in joined

    def test_passthrough_for_long_ls_format(self):
        input_lines = lines(
            "total 2",
            "-rw-r--r--  1 ed  staff   10 Mar 25 10:00 file.txt",
            "drwxr-xr-x  4 ed  staff  128 Mar 25 10:00 src",
        )
        result = _compress_listing(input_lines)
        assert result == input_lines

    def test_dispatches_for_fd_find_and_ls(self):
        data = b"dup.py\ndup.py\nunique.md\n"
        for command in ("fd", "find", "ls"):
            compressed, orig, comp = compress(command, data)
            assert compressed == b"dup.py\nunique.md\n"
            assert orig == 3
            assert comp == 2

    def test_respects_env_max_entries_override(self, monkeypatch):
        monkeypatch.setenv("WUMW_LISTING_MAX_ENTRIES", "3")
        input_lines = lines(
            "src/a.py",
            "src/b.py",
            "src/c.py",
            "src/d.py",
            "docs/readme.md",
        )
        result = _compress_listing(input_lines)

        sampled_entries = [line for line in result if not line.startswith(b"#")]
        assert len(sampled_entries) <= 3


# ---------------------------------------------------------------------------
# 7. generic fallback — collapses repeated lines, truncates at 200
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

    def test_respects_env_repeat_and_truncate_overrides(self, monkeypatch):
        monkeypatch.setenv("WUMW_GENERIC_REPEAT_THRESHOLD", "1")
        monkeypatch.setenv("WUMW_GENERIC_LINES", "2")
        input_lines = [b"same\n", b"same\n", b"tail 1\n", b"tail 2\n"]
        result = _compress_generic(input_lines)

        assert result[0] == b"same\n"
        assert b"repeated 2 times" in result[1]
        assert b"truncated" in result[-1]


# ---------------------------------------------------------------------------
# 8. --full flag — bypasses compression, raw output
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
        log_file = log_dir() / f"{session_id}.jsonl"
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
        """When compression meaningfully reduces line count, a header appears."""
        env = {"WUMW_SESSION": "test-header-reduced"}
        # cat compressor strips # comment lines in .py files — use a .py file full of them
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
            # 50 comment lines + 1 code line → should reduce
            f.write(b"# comment\n" * 50 + b"code = 1\n")
            tmpname = f.name
        try:
            r = run_wumw("cat", tmpname, env=env)
            assert b"# wumw:" in r.stdout
        finally:
            os.unlink(tmpname)

    def test_header_absent_when_reduction_is_tiny(self):
        """Tiny reductions should stay silent to avoid adding context noise."""
        env = {"WUMW_SESSION": "test-header-small-reduction"}
        r = run_wumw("sh", "-c", "printf 'same\\nsame\\nsame\\nsame\\nkeep\\n'", env=env)
        assert b"# wumw:" not in r.stdout

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

    def test_header_threshold_can_be_overridden_by_env(self):
        env = {
            "WUMW_SESSION": "test-header-env-override",
            "WUMW_HEADER_MIN_SAVED": "1",
        }
        r = run_wumw("sh", "-c", "printf 'same\\nsame\\nsame\\nsame\\nkeep\\n'", env=env)
        assert b"# wumw:" in r.stdout


# ---------------------------------------------------------------------------
# 9. JSONL logging — correct fields written to .wumw/sessions/
# ---------------------------------------------------------------------------

class TestJsonlLogging:
    def _run_and_read_log(self, *args, extra_env=None):
        session_id = f"test-log-{os.getpid()}-{id(args)}"
        log_file = log_dir() / f"{session_id}.jsonl"
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
        log_file = log_dir() / f"{session_id}.jsonl"
        if log_file.exists():
            log_file.unlink()
        env = {"WUMW_SESSION": session_id}
        run_wumw("true", env=env)
        entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
        assert entries[-1]["session_id"] == session_id

    def test_log_includes_session_context_fields(self):
        entries = self._run_and_read_log("true")
        entry = entries[-1]
        assert "session_started_at" in entry
        assert entry["cwd"] == str(REPO_ROOT)
        assert entry["context_root"] == str(REPO_ROOT)
        assert entry["in_git_repo"] is True

    def test_codex_thread_id_becomes_session_id(self):
        thread_id = f"thread-{os.getpid()}"
        log_file = log_dir() / f"{thread_id}.jsonl"
        if log_file.exists():
            log_file.unlink()

        run_wumw("true", env={"CODEX_THREAD_ID": thread_id})

        entries = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
        assert entries[-1]["session_id"] == thread_id

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

    def test_log_has_estimated_tokens(self):
        entries = self._run_and_read_log("sh", "-c", "printf hello")
        assert entries[-1]["estimated_tokens"] == 2

    def test_log_estimated_tokens_zero_for_empty_stdout(self):
        entries = self._run_and_read_log("true")
        assert entries[-1]["estimated_tokens"] == 0

    def test_log_has_stdout_lines(self):
        entries = self._run_and_read_log("sh", "-c", "printf 'a\\nb\\nc\\n'")
        assert entries[-1]["stdout_lines"] == 3

    def test_log_has_stderr_bytes(self):
        entries = self._run_and_read_log("sh", "-c", "printf err >&2")
        assert "stderr_bytes" in entries[-1]
        assert entries[-1]["stderr_bytes"] == 3

    def test_log_compressed_lines_present_when_reduced(self):
        """compressed_lines field should appear when compression reduced output."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
            # Many hash comments — cat compressor strips them in .py files
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

    def test_log_compressed_lines_absent_for_binary_stdout(self):
        entries = self._run_and_read_log(*BINARY_STDOUT_CMD)
        assert "compressed_lines" not in entries[-1]

    def test_log_appends_multiple_entries(self):
        session_id = f"test-log-append-{os.getpid()}"
        log_file = log_dir() / f"{session_id}.jsonl"
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


class TestStateFallback:
    def test_cli_works_outside_git_repo_with_xdg_state_home(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        state_home = tmp_path / "state-home"

        r = run_wumw(
            "--full",
            "echo",
            "ok",
            cwd=outside,
            env={"WUMW_HOME": "", "XDG_STATE_HOME": str(state_home)},
        )

        assert r.returncode == 0
        assert r.stdout == b"ok\n"
        sessions = list((state_home / "wumw").rglob("*.jsonl"))
        assert sessions

    def test_get_state_dir_prefers_repo_local_when_writable(self, monkeypatch, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        monkeypatch.delenv("WUMW_HOME", raising=False)
        monkeypatch.setattr(state, "find_repo_root", lambda: repo_root)

        state_dir = state.get_state_dir()

        assert state_dir == repo_root / ".wumw"

    def test_get_state_dir_falls_back_when_repo_state_not_writable(self, monkeypatch, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        state_home = tmp_path / "state-home"
        monkeypatch.delenv("WUMW_HOME", raising=False)
        monkeypatch.setattr(state, "find_repo_root", lambda: repo_root)
        monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
        original_ensure_dir = state._ensure_dir

        def fake_ensure_dir(path):
            if path == repo_root / ".wumw":
                return False
            return original_ensure_dir(path)

        monkeypatch.setattr(state, "_ensure_dir", fake_ensure_dir)

        state_dir = state.get_state_dir()

        assert state_dir != repo_root / ".wumw"
        assert state_dir.parent == state_home / "wumw"

    def test_get_state_dir_falls_back_to_temp_when_default_state_unwritable(self, monkeypatch, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        state_home = tmp_path / "state-home"
        temp_dir = tmp_path / "tmp-state"
        default_state_dir = state_home / "wumw" / "repo-default"
        monkeypatch.delenv("WUMW_HOME", raising=False)
        monkeypatch.setattr(state, "find_repo_root", lambda: repo_root)
        monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
        monkeypatch.setattr(state.tempfile, "gettempdir", lambda: str(temp_dir))
        original_ensure_dir = state._ensure_dir

        def fake_ensure_dir(path):
            if path in {repo_root / ".wumw", default_state_dir}:
                return False
            return original_ensure_dir(path)

        monkeypatch.setattr(state, "_fallback_state_dir", lambda *_: default_state_dir)
        monkeypatch.setattr(state, "_ensure_dir", fake_ensure_dir)

        state_dir = state.get_state_dir()

        assert state_dir.parent == temp_dir / "wumw"

    def test_get_session_info_uses_explicit_wumw_home(self, monkeypatch, tmp_path):
        wumw_home = tmp_path / "custom-state"
        monkeypatch.setenv("WUMW_HOME", str(wumw_home))
        monkeypatch.delenv("WUMW_SESSION", raising=False)
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)

        session_info = state.get_session_info()

        assert session_info["session_id"]
        assert (wumw_home / "session").exists()

    def test_get_session_info_uses_codex_thread_id_without_persisting_session_file(
        self, monkeypatch, tmp_path
    ):
        wumw_home = tmp_path / "custom-state"
        monkeypatch.setenv("WUMW_HOME", str(wumw_home))
        monkeypatch.delenv("WUMW_SESSION", raising=False)
        monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread-123")

        session_info = state.get_session_info()

        assert session_info["session_id"] == "codex-thread-123"
        assert not (wumw_home / "session").exists()

    def test_get_session_info_rotates_after_idle_timeout(self, monkeypatch, tmp_path):
        wumw_home = tmp_path / "custom-state"
        monkeypatch.setenv("WUMW_HOME", str(wumw_home))
        monkeypatch.delenv("WUMW_SESSION", raising=False)
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)

        first = state.get_session_info()
        session_file = wumw_home / "session"
        record = json.loads(session_file.read_text())
        record["last_used_at"] = "2000-01-01T00:00:00+00:00"
        session_file.write_text(json.dumps(record))

        rotated = state.get_session_info()

        assert rotated["session_id"] != first["session_id"]


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
        compressed, orig, comp = compress("cat", data, args=["module.py"])
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

    def test_binary_output_uses_passthrough(self):
        data = b"\x00\xffsame line\n\x01tail"
        compressed, orig, comp = compress("cat", data)
        assert compressed == data
        assert orig == comp == 2

    def test_binary_detector_ignores_utf8_text(self):
        assert is_probably_binary("caf\u00e9\nna\u00efve\n".encode("utf-8")) is False

    def test_binary_detector_flags_binary_like_bytes(self):
        assert is_probably_binary(b"\x00\xffsame line\n\x01tail") is True


class TestSavings:
    def test_summarize_savings_accounts_for_compressed_and_full_entries(self):
        entries = [
            {
                "command": "cat",
                "stdout_lines": 100,
                "compressed_lines": 10,
                "stdout_bytes": 1000,
            },
            {
                "command": "rg",
                "stdout_lines": 20,
                "compressed_lines": 5,
                "stdout_bytes": 200,
            },
            {
                "command": "git",
                "stdout_lines": 30,
                "stdout_bytes": 300,
                "full": True,
            },
        ]

        summary = savings.summarize_savings(entries)

        assert summary["total_calls"] == 3
        assert summary["compressed_calls"] == 2
        assert summary["full_calls"] == 1
        assert summary["raw_lines"] == 150
        assert summary["effective_lines"] == 45
        assert summary["saved_lines"] == 105
        assert summary["line_savings_pct"] == pytest.approx(70.0)
        assert summary["raw_bytes"] == 1500
        assert summary["effective_bytes_estimate"] == pytest.approx(450.0)
        assert summary["saved_bytes_estimate"] == pytest.approx(1050.0)
        assert summary["saved_tokens_estimate"] == pytest.approx(262.5)
        assert [row["command"] for row in summary["by_command"]] == ["cat", "rg", "git"]

    def test_summarize_savings_uses_raw_output_when_not_compressed(self):
        entries = [
            {
                "command": "cat",
                "stdout_lines": 0,
                "stdout_bytes": 0,
            },
            {
                "command": "git",
                "stdout_lines": 10,
                "stdout_bytes": 100,
            },
        ]

        summary = savings.summarize_savings(entries)

        assert summary["effective_lines"] == 10
        assert summary["saved_lines"] == 0
        assert summary["effective_bytes_estimate"] == pytest.approx(100.0)

    def test_summarize_groups_breaks_down_by_session(self):
        entries = [
            {
                "timestamp": "2026-03-25T10:00:00+00:00",
                "session_id": "session-a",
                "command": "cat",
                "stdout_lines": 10,
                "compressed_lines": 5,
                "stdout_bytes": 100,
            },
            {
                "timestamp": "2026-03-25T11:00:00+00:00",
                "session_id": "session-b",
                "command": "rg",
                "stdout_lines": 20,
                "compressed_lines": 10,
                "stdout_bytes": 200,
            },
        ]

        rows = savings.summarize_groups(
            entries,
            key_fn=lambda entry: entry["session_id"],
            bytes_per_token=4.0,
        )

        assert [row["key"] for row in rows] == ["session-a", "session-b"]
        assert rows[0]["summary"]["saved_tokens_estimate"] == pytest.approx(12.5)
        assert rows[1]["summary"]["saved_tokens_estimate"] == pytest.approx(25.0)

    def test_load_entries_applies_since_and_until_filters(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "demo.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": "2026-03-25T09:00:00+00:00",
                            "session_id": "demo",
                            "command": "cat",
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-03-25T12:00:00+00:00",
                            "session_id": "demo",
                            "command": "rg",
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-03-25T15:00:00+00:00",
                            "session_id": "demo",
                            "command": "git",
                        }
                    ),
                ]
            )
            + "\n"
        )

        entries = savings.load_entries(
            sessions_dir,
            since=savings.parse_datetime("2026-03-25T10:00:00+00:00"),
            until=savings.parse_datetime("2026-03-25T13:00:00+00:00"),
        )

        assert [entry["command"] for entry in entries] == ["rg"]
