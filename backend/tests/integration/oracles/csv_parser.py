"""Oracle for whitepapers/10_csv_parser.md.

Authored from the whitepaper only; never shown to any agent.

CSV is the whitepaper where a wrong implementation looks most convincing. Split
on newlines, then split each line on commas, and every flat document parses
perfectly — the failure only appears when a quoted field contains a delimiter, a
quotechar or a newline, which is the one thing the format exists to support. So
the cases here are weighted towards quoted-field content, towards the blank-line
versus empty-field distinction of §3.2 that no flat-splitting parser can express,
and towards two properties no single call reveals: chunk invariance (§7.3) and
agreement with the stdlib ``csv`` module, which the generated code is forbidden
from importing (§10) but which is a perfect differential oracle here.
"""

from __future__ import annotations

import csv
import functools
import io
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _safe(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Turn a raise from the generated code into a reported failure.

    ``run_oracle`` guards the *call* of a case target but not the ``check``
    itself, so an exception escaping a multi-step check aborts the whole oracle
    run instead of being collected. Every check below drives generated code
    across many operations, so every one of them needs this.
    """

    @functools.wraps(check)
    def wrapper(obj: Any) -> bool:
        try:
            return check(obj)
        except Exception:  # noqa: BLE001 — any raise mid-property is a failure
            return False

    return wrapper

# ── stdlib differential oracle ───────────────────────────────────────────────
# The generated module may not import `csv` (§10). This oracle may, and does:
# CPython's reader and writer implement exactly the dialect §3 and §5 specify,
# so any divergence on well-formed input is a defect in the generated code.


def _std_read(text: str, **dialect: Any) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline=""), **dialect))


def _std_write(rows: list[list[str]], **dialect: Any) -> str:
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\r\n", **dialect).writerows(rows)
    return buf.getvalue()


_ALPHABET = ("a", "b", "Z", "9", " ", ",", '"', "\n", "\r\n", "\r", "\t", ";", "e", "")


def _random_rows(rng: random.Random, extra: tuple[str, ...] = ()) -> list[list[str]]:
    """Records drawn from an alphabet that is mostly structural characters.

    Fields routinely contain delimiters, bare and doubled quotes, CR, LF and
    CRLF, because that is the region where a flat-splitting parser fails and a
    state machine does not.
    """
    pool = _ALPHABET + extra
    return [
        [
            "".join(rng.choice(pool) for _ in range(rng.randint(0, 4)))
            for _ in range(rng.randint(0, 4))
        ]
        for _ in range(rng.randint(0, 4))
    ]


def _get(obj: Any, *names: str) -> dict[str, Any] | None:
    """Resolve sibling public names from the module that defines ``obj``.

    Properties such as round-tripping need the reader *and* the writer, but a
    check only receives one resolved object. The agent may have split the API
    across modules, so the search widens to any generated module in the same
    directory. Returns None rather than raising: the framework treats a False
    return as a reportable failure but lets an exception escape.
    """
    module = sys.modules.get(getattr(obj, "__module__", ""))
    if module is None:
        return None
    root = Path(getattr(module, "__file__", "") or ".").parent
    found: dict[str, Any] = {}
    for name in names:
        if hasattr(module, name):
            found[name] = getattr(module, name)
            continue
        for other in list(sys.modules.values()):
            path = getattr(other, "__file__", None)
            if path and Path(path).parent == root and hasattr(other, name):
                found[name] = getattr(other, name)
                break
        else:
            return None
    return found


# ── properties no single call reveals ────────────────────────────────────────


@_safe
def _matches_stdlib_reader(parse_csv: Any) -> bool:
    """§3 — differential against csv.reader over 400 hostile documents.

    The corpus is built with csv.writer, so every document is well-formed and
    the only divergence possible is a misreading of quoting, escaping or record
    termination.
    """
    rng = random.Random(20260810)
    for _ in range(400):
        text = _std_write(_random_rows(rng))
        if parse_csv(text) != _std_read(text):
            return False
    return True


@_safe
def _matches_stdlib_writer(format_csv: Any) -> bool:
    """§5 — differential against csv.writer, minimal and all-quoting."""
    rng = random.Random(20260811)
    for _ in range(400):
        rows = _random_rows(rng)
        if format_csv(rows) != _std_write(rows):
            return False
        if format_csv(rows, quoting="all") != _std_write(rows, quoting=csv.QUOTE_ALL):
            return False
    return True


@_safe
def _round_trips(format_csv: Any) -> bool:
    """§7.4 — parse(format(rows)) == rows, under both quoting policies.

    This is where the §5 sole-empty-field rule bites: a writer that emits ``['']``
    as a bare empty line reads it back as the empty record ``[]`` and fails here.
    """
    api = _get(format_csv, "parse_csv")
    if api is None:
        return False
    parse_csv = api["parse_csv"]
    rng = random.Random(20260812)
    for _ in range(400):
        rows = _random_rows(rng)
        if parse_csv(format_csv(rows)) != rows:
            return False
        if parse_csv(format_csv(rows, quoting="all")) != rows:
            return False
    return True


@_safe
def _writer_is_idempotent(format_csv: Any) -> bool:
    """§7.5 — format(parse(format(rows))) == format(rows)."""
    api = _get(format_csv, "parse_csv")
    if api is None:
        return False
    rng = random.Random(20260813)
    for _ in range(200):
        once = format_csv(_random_rows(rng))
        if format_csv(api["parse_csv"](once)) != once:
            return False
    return True


@_safe
def _is_chunk_invariant(reader_cls: Any) -> bool:
    """§7.3 — the record stream does not depend on how the input was chunked.

    Single-character feeding is the strongest form: it forces genuine incremental
    state, and it splits every CRLF pair and every ``""`` escape across a
    boundary. An implementation that buffers everything and parses at close()
    passes this; one that parses each chunk independently cannot.
    """
    api = _get(reader_cls, "parse_csv")
    if api is None:
        return False
    parse_csv = api["parse_csv"]
    rng = random.Random(20260814)
    for _ in range(120):
        text = _std_write(_random_rows(rng))
        expected = parse_csv(text)

        one_at_a_time: list[list[str]] = []
        reader = reader_cls()
        for char in text:
            one_at_a_time.extend(reader.feed(char))
        one_at_a_time.extend(reader.close())
        if one_at_a_time != expected:
            return False

        chunked: list[list[str]] = []
        reader = reader_cls()
        pos = 0
        while pos < len(text):
            size = rng.randint(1, 5)
            chunked.extend(reader.feed(text[pos : pos + size]))
            pos += size
        chunked.extend(reader.close())
        if chunked != expected:
            return False
    return True


@_safe
def _crlf_split_across_chunks_is_one_terminator(reader_cls: Any) -> bool:
    """§3.1 — a CR ending a chunk must not produce a spurious empty record.

    The record is emitted on the CR and the LF opening the next chunk is
    discarded. A reader that instead waits for the LF, or that treats the
    orphaned LF as a fresh blank line, yields three records here instead of two.
    """
    reader = reader_cls()
    records: list[list[str]] = []
    for chunk in ("a,b\r", "\nc,d\r", "\n"):
        records.extend(reader.feed(chunk))
    records.extend(reader.close())
    return bool(records == [["a", "b"], ["c", "d"]])


@_safe
def _feed_defers_incomplete_records(reader_cls: Any) -> bool:
    """§4 — feed() returns only *completed* records; the partial one is held."""
    reader = reader_cls()
    if reader.feed("a,b") != []:
        return False
    if reader.feed(",c\n") != [["a", "b", "c"]]:
        return False
    if reader.feed('"x') != []:
        return False
    if reader.feed('y"\n') != [["xy"]]:
        return False
    return bool(reader.close() == [])


@_safe
def _reader_is_reusable_after_close(reader_cls: Any) -> bool:
    """§7.6 — close() restores the initial state, including the CR flag.

    The second document starts with LF. A reader whose `after_cr` survived the
    close swallows it and loses the leading blank record.
    """
    reader = reader_cls()
    first = reader.feed("a\r")
    first.extend(reader.close())
    if first != [["a"]]:
        return False
    second = reader.feed("\nb")
    second.extend(reader.close())
    if second != [[], ["b"]]:
        return False
    fresh = reader_cls()
    baseline = fresh.feed("\nb")
    baseline.extend(fresh.close())
    return bool(second == baseline)


@_safe
def _quoted_fields_are_transparent(parse_csv: Any) -> bool:
    """§7.2 — a correctly escaped quoted field reproduces any string at all."""
    rng = random.Random(20260815)
    for _ in range(600):
        field = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 6)))
        if parse_csv('"' + field.replace('"', '""') + '"') != [[field]]:
            return False
    return True


@_safe
def _is_dialect_agnostic(format_csv: Any) -> bool:
    """§7.7 — the comma and the double quote are not privileged.

    Fields are drawn from a pool that includes the custom delimiter and
    quotechar, so a parser with a hard-coded comma or a writer that escapes only
    `"` breaks immediately.
    """
    api = _get(format_csv, "parse_csv")
    if api is None:
        return False
    parse_csv = api["parse_csv"]
    rng = random.Random(20260816)
    for delimiter, quotechar in (("|", "'"), (";", '"'), ("\t", "~"), (",", "#")):
        dialect = {"delimiter": delimiter, "quotechar": quotechar}
        for _ in range(60):
            rows = _random_rows(rng, extra=(delimiter, quotechar))
            text = format_csv(rows, **dialect)
            if parse_csv(text, **dialect) != rows:
                return False
            if parse_csv(text, **dialect) != _std_read(text, **dialect):
                return False
    return True


@_safe
def _handles_a_large_document(parse_csv: Any) -> bool:
    """§6 — single pass, iterative. 4000 records of quoted, multi-line fields.

    A reader that recurses per record or per character hits the recursion limit
    and fails outright here; a re-scanning one (``text = text[1:]``) still
    answers correctly but becomes conspicuously slow at this size.
    """
    api = _get(parse_csv, "format_csv")
    if api is None:
        return False
    rows = [[f"id{i}", 'a,b"c', "line one\nline two", str(i)] for i in range(4000)]
    return bool(parse_csv(api["format_csv"](rows)) == rows)


@_safe
def _parsing_is_deterministic(parse_csv: Any) -> bool:
    """§7.6 — no state leaks between calls."""
    text = 'a,"b,c"\r\n\r\n"d""e","f\ng"\r\nh'
    first = parse_csv(text)
    return bool(first == parse_csv(text) == parse_csv(text) == _std_read(text))


ORACLE = Oracle(
    whitepaper="10_csv_parser.md",
    package_hint="csv",
    required_names=[
        "parse_csv",
        "format_csv",
        "format_row",
        "needs_quoting",
        "CsvReader",
        "CsvError",
        "QUOTE_MINIMAL",
        "QUOTE_ALL",
        "QUOTE_NONE",
    ],
    cases=[
        # ── §3, the state machine on worked examples ────────────────────────
        Case(
            target="parse_csv",
            args=("a,b,c\n",),
            expected=[["a", "b", "c"]],
            description="§3 a plain record",
        ),
        Case(
            target="parse_csv",
            args=('"a,b",c',),
            expected=[["a,b", "c"]],
            description="§3 a delimiter inside quotes is data, not a field break",
        ),
        Case(
            target="parse_csv",
            args=('"say ""hi""",x',),
            expected=[['say "hi"', "x"]],
            description="§3 the doubled-quote escape yields one quotechar",
        ),
        Case(
            target="parse_csv",
            args=('"line one\nline two",b\nc,d',),
            expected=[["line one\nline two", "b"], ["c", "d"]],
            description="§3 a newline inside quotes does not end the record",
        ),
        Case(
            target="parse_csv",
            args=('"a\r\nb"',),
            expected=[["a\r\nb"]],
            description="§3 a CRLF inside quotes is preserved verbatim",
        ),
        Case(
            target="parse_csv",
            args=('"",""\n',),
            expected=[["", ""]],
            description="§3 empty quoted fields",
        ),
        # ── §3.1, record termination ────────────────────────────────────────
        Case(
            target="parse_csv",
            args=("a\r\nb\rc\nd",),
            expected=[["a"], ["b"], ["c"], ["d"]],
            description="§3.1 CRLF, lone CR and lone LF each end exactly one record",
        ),
        Case(
            target="parse_csv",
            args=("a,b",),
            expected=[["a", "b"]],
            description="§3.2 a final record without a terminator is not dropped",
        ),
        Case(
            target="parse_csv",
            args=("a,b\r\n",),
            expected=[["a", "b"]],
            description="§3.2 a trailing terminator does not add an empty record",
        ),
        Case(
            target="parse_csv",
            args=("a,\nb,",),
            expected=[["a", ""], ["b", ""]],
            description="§3 a trailing delimiter commits an empty field, at EOL and EOF",
        ),
        # ── §3.2, the distinction a flat splitter cannot make ───────────────
        Case(
            target="parse_csv",
            args=('a\n\n""\n',),
            expected=[["a"], [], [""]],
            description="§3.2 blank line is [] but a quoted empty field is ['']",
        ),
        Case(
            target="parse_csv",
            args=("\r\n\r\n",),
            expected=[[], []],
            description="§8 terminators only give two empty records",
        ),
        Case(
            target="parse_csv",
            args=(",,",),
            expected=[["", "", ""]],
            description="§8 two delimiters give three empty fields",
        ),
        Case(
            target="parse_csv",
            args=("",),
            expected=[],
            description="§8 empty input gives no records",
        ),
        Case(
            target="parse_csv",
            args=(" a , b ",),
            expected=[[" a ", " b "]],
            description="§5 whitespace around fields is never stripped",
        ),
        # ── §3.3, lenient recovery ──────────────────────────────────────────
        Case(
            target="parse_csv",
            args=('a"b,c',),
            expected=[['a"b', "c"]],
            description="§3.3 a bare quote in an unquoted field is literal",
        ),
        Case(
            target="parse_csv",
            args=('"ab"c,d',),
            expected=[["abc", "d"]],
            description="§3.3 text after a closing quote continues as *unquoted*",
        ),
        Case(
            target="parse_csv",
            args=("a|'b|c'",),
            kwargs={"delimiter": "|", "quotechar": "'"},
            expected=[["a", "b|c"]],
            description="§2 a custom dialect quotes and splits on its own characters",
        ),
        # ── §5, the writer ──────────────────────────────────────────────────
        Case(
            target="needs_quoting",
            args=("plain text",),
            expected=False,
            description="§9 an ordinary field needs no quoting",
        ),
        Case(
            target="needs_quoting",
            args=('a"b',),
            expected=True,
            description="§9 a field containing the quotechar needs quoting",
        ),
        Case(
            target="needs_quoting",
            args=("a\rb",),
            expected=True,
            description="§9 a field containing CR needs quoting",
        ),
        Case(
            target="needs_quoting",
            args=("a,b",),
            kwargs={"delimiter": "|"},
            expected=False,
            description="§9 the comma is not special under a custom delimiter",
        ),
        Case(
            target="format_row",
            args=(["a,b", 'c"d', "e\nf", "g"],),
            expected='"a,b","c""d","e\nf",g',
            description="§5 QUOTE_MINIMAL quotes exactly the fields that need it",
        ),
        Case(
            target="format_row",
            args=([""],),
            expected='""',
            description="§5 a sole empty field is quoted so it does not read back as []",
        ),
        Case(
            target="format_row",
            args=(["", ""],),
            expected=",",
            description="§5 two empty fields need no quoting",
        ),
        Case(
            target="format_row",
            args=([],),
            expected="",
            description="§5 the empty record renders as an empty line",
        ),
        Case(
            target="format_row",
            args=(["a", "b"],),
            kwargs={"quoting": "all"},
            expected='"a","b"',
            description="§5/§9 QUOTE_ALL is the documented string 'all'",
        ),
        Case(
            target="format_row",
            args=(["a", "b"],),
            kwargs={"quoting": "none"},
            expected="a,b",
            description="§5/§9 QUOTE_NONE is the documented string 'none'",
        ),
        Case(
            target="format_csv",
            args=([[1, None, 2.5]],),
            expected="1,,2.5\r\n",
            description="§5 None renders empty and other values via str()",
        ),
        Case(
            target="format_csv",
            args=([],),
            expected="",
            description="§8 no records render as the empty string",
        ),
        Case(
            target="format_csv",
            args=([["a"], []],),
            kwargs={"lineterminator": "\n"},
            expected="a\n\n",
            description="§5 lineterminator follows every record including the last",
        ),
        # ── properties no single call reveals ───────────────────────────────
        Case(
            target="parse_csv",
            call=False,
            check=_matches_stdlib_reader,
            description="§3 agrees with csv.reader on 400 hostile documents",
        ),
        Case(
            target="format_csv",
            call=False,
            check=_matches_stdlib_writer,
            description="§5 agrees with csv.writer, minimal and all-quoting",
        ),
        Case(
            target="format_csv",
            call=False,
            check=_round_trips,
            description="§7.4 parse(format(rows)) == rows for random records",
        ),
        Case(
            target="format_csv",
            call=False,
            check=_writer_is_idempotent,
            description="§7.5 format(parse(format(rows))) == format(rows)",
        ),
        Case(
            target="CsvReader",
            call=False,
            check=_is_chunk_invariant,
            description="§7.3 records are identical under 1-char and random chunking",
        ),
        Case(
            target="CsvReader",
            call=False,
            check=_crlf_split_across_chunks_is_one_terminator,
            description="§3.1 a CRLF split across chunks is one terminator",
        ),
        Case(
            target="CsvReader",
            call=False,
            check=_feed_defers_incomplete_records,
            description="§4 feed() withholds the partial record until it completes",
        ),
        Case(
            target="CsvReader",
            call=False,
            check=_reader_is_reusable_after_close,
            description="§7.6 close() restores the initial state, CR flag included",
        ),
        Case(
            target="parse_csv",
            call=False,
            check=_quoted_fields_are_transparent,
            description="§7.2 an escaped quoted field reproduces any string",
        ),
        Case(
            target="format_csv",
            call=False,
            check=_is_dialect_agnostic,
            description="§7.7 no hard-coded comma or double quote",
        ),
        Case(
            target="parse_csv",
            call=False,
            check=_handles_a_large_document,
            description="§6 4000 multi-line records parse in a single iterative pass",
        ),
        Case(
            target="parse_csv",
            call=False,
            check=_parsing_is_deterministic,
            description="§7.6 repeated parses of one document agree",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="parse_csv",
            args=('a,"bc',),
            exc_name="CsvError",
            description="§3.3 an unterminated quoted field raises even leniently",
        ),
        ErrorCase(
            target="parse_csv",
            args=('a"b',),
            kwargs={"strict": True},
            exc_name="CsvError",
            description="§3.3 strict mode rejects a bare quote in an unquoted field",
        ),
        ErrorCase(
            target="parse_csv",
            args=('"ab"c',),
            kwargs={"strict": True},
            exc_name="CsvError",
            description="§3.3 strict mode rejects text after a closing quote",
        ),
        ErrorCase(
            target="parse_csv",
            args=(123,),
            exc_name="CsvError",
            description="§8 a non-str document raises rather than being iterated",
        ),
        ErrorCase(
            target="parse_csv",
            args=("a,b",),
            kwargs={"delimiter": ",,"},
            exc_name="CsvError",
            description="§2 a multi-character delimiter raises",
        ),
        ErrorCase(
            target="parse_csv",
            args=("a,b",),
            kwargs={"delimiter": ",", "quotechar": ","},
            exc_name="CsvError",
            description="§2 delimiter and quotechar must differ",
        ),
        ErrorCase(
            target="parse_csv",
            args=("a,b",),
            kwargs={"delimiter": "\n"},
            exc_name="CsvError",
            description="§2 a CR or LF delimiter raises",
        ),
        ErrorCase(
            target="CsvReader",
            kwargs={"quotechar": ""},
            exc_name="CsvError",
            description="§8 the dialect is validated in the constructor",
        ),
        ErrorCase(
            target="format_csv",
            args=([["a,b"]],),
            kwargs={"quoting": "none"},
            exc_name="CsvError",
            description="§5 QUOTE_NONE raises rather than emitting unreadable output",
        ),
        ErrorCase(
            target="format_row",
            args=(["a"],),
            kwargs={"quoting": "fancy"},
            exc_name="CsvError",
            description="§8 an unknown quoting mode raises",
        ),
        ErrorCase(
            target="format_row",
            args=("ab",),
            exc_name="CsvError",
            description="§8 a str record raises rather than being split into characters",
        ),
        ErrorCase(
            target="format_csv",
            args=([["a"]],),
            kwargs={"lineterminator": "||"},
            exc_name="CsvError",
            description="§2 lineterminator must be CRLF, LF or CR",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§10 forbids delegating to the csv module — the character-level "
                "state machine is the entire deliverable, and a wrapper around "
                "csv.reader would satisfy every functional test while "
                "implementing nothing"
            ),
            imports=("csv", "_csv", "pandas"),
        ),
        Prohibition(
            reason=(
                "§10 forbids a regex scanner — with embedded newlines the grammar "
                "is context-sensitive across record boundaries, which is why the "
                "specified design is a state machine"
            ),
            imports=("re",),
        ),
        Prohibition(
            reason=(
                "§10 forbids splitting the input — str.split/rsplit/splitlines "
                "cannot honour a delimiter or a newline inside a quoted field, "
                "which is the whole problem"
            ),
            attr_calls=("split", "rsplit", "splitlines"),
        ),
    ],
)
