# RFC 4180 CSV Reader and Writer with Quoting and Embedded Newlines

Python Library Specification

## Abstract

This document specifies a Python library that reads and writes delimiter-separated
text according to RFC 4180. The reader is a character-level state machine: it
consumes one character at a time and never splits the input on delimiters or on
line breaks, because inside a quoted field both are ordinary data. It is
incremental — a document may be fed in arbitrary chunks, including chunks that cut
a quoted field, a `""` escape or a CRLF pair in half — and yields identical
records however it was chunked. The writer is the exact inverse: reading back what
was written reproduces the records.

## 1. Overview and Design Rationale

The naive CSV reader splits the document on newlines and each line on commas. It
is wrong for every document that uses the format's only interesting feature:
`"Smith, John","said ""no""","line one<LF>line two"` is one record of three
fields, and splitting produces five broken pieces of two records. Correctness
requires tracking, per character, whether the reader is inside a quoted field.

Four states suffice (§3). The reader also keeps a field buffer, a record list, a
flag `started` recording whether any character of the current record has been
consumed, and a flag `after_cr` for the CRLF rule of §3.1. No lookahead and no
backtracking are required, which is what makes chunked input work.

## 2. Dialect Parameters

| Name | Default | Meaning |
|---|---|---|
| `delimiter` | `,` | Separates fields within a record |
| `quotechar` | `"` | Delimits a quoted field; doubled inside one to mean itself |
| `strict` | `False` | Raise on malformed quoting rather than recovering (§3.3) |
| `quoting` | `QUOTE_MINIMAL` | Writer policy (§5) |
| `lineterminator` | `\r\n` | Written after every record; must be `\r\n`, `\n` or `\r` |

`delimiter` and `quotechar` must each be a one-character string, must differ from
each other, and must be neither CR nor LF. The reader always accepts all three
line terminators on input regardless of `lineterminator`, which is a writer-only
setting.

## 3. The Reader State Machine

States: `START` (at the beginning of a field), `UNQUOTED`, `QUOTED`,
`AFTER_QUOTE` (a quotechar was seen while in `QUOTED` and its meaning is not yet
decided). `D` is the delimiter, `Q` the quotechar, and `T` is CR or LF.

| State | On `Q` | On `D` | On `T` | On EOF | Otherwise |
|---|---|---|---|---|---|
| `START` | → `QUOTED` | emit empty field, stay `START` | end record (§3.1) | end record if `started` | append, → `UNQUOTED` |
| `UNQUOTED` | §3.3 lenient: append; strict: raise | end field, → `START` | end field, end record | end field, end record | append |
| `QUOTED` | → `AFTER_QUOTE` | append (data) | append (data) | **raise** — unterminated | append |
| `AFTER_QUOTE` | append one `Q`, → `QUOTED` | end field, → `START` | end field, end record | end field, end record | §3.3 lenient: append, → `UNQUOTED`; strict: raise |

Consuming any character other than a record terminator sets `started`. "End
record" means precisely: if `started`, end the current field and emit the record
including that field; otherwise emit the empty record (§3.2). So `"a,"` followed
by a terminator is `['a', '']`, not `['a']` — the delimiter already committed a
second field.

The `QUOTED` row is the whole point of the specification: `D`, CR and LF are
appended verbatim, so a quoted field may contain delimiters, embedded newlines
and — via the `AFTER_QUOTE` row — quotechars. Nothing inside a quoted field is
transformed except the `QQ` pair, which yields a single `Q`.

### 3.1 Record Termination and the CR/LF Rule

LF ends the current record. CR also ends it **immediately** and sets `after_cr`;
if the very next character consumed is LF it is discarded and has no further
effect. The record is emitted on the CR rather than held back awaiting an LF, so
a chunk boundary falling between CR and LF cannot change the output. `after_cr`
is cleared by any character.

### 3.2 Blank Lines Versus the Empty Field

A record terminator reached with `started` false — a blank line — produces the
**empty record `[]`**, a record with zero fields. This is distinct from a line
containing `""`, which produces `['']`, one empty field. An implementation
treating these alike cannot round-trip (§5, §7.4). Reaching end of input with
`started` false produces no record at all, so a document's final terminator does
not manufacture a trailing empty record; reaching it with `started` true emits
the pending record, so a last line lacking a terminator is not lost.

### 3.3 Lenient and Strict Modes

Two malformed constructs are recovered from when `strict` is false and raise
`CsvError` when it is true:

1. A quotechar inside an unquoted field (`a"b`) — leniently a literal character,
   giving `a"b`.
2. Any character other than `Q`, `D` or a terminator following a closing quote
   (`"ab"c`) — leniently appended to the field, which continues as an *unquoted*
   field, giving `abc`. Continuing as a quoted field would be wrong: the following
   `,` in `"ab"c,d` is a delimiter, not data.

An unterminated quoted field at end of input always raises, in both modes; there
is no sensible recovery, since every remaining character was consumed as data.

## 4. Incremental Reading

`CsvReader.feed(chunk)` returns the records **completed** by that chunk — possibly
none — retaining any partial record in its state. `close()` returns the pending
record as a one-element list, or an empty list if none is pending, and raises in
state `QUOTED`. `parse_csv(text)` is `feed(text)` followed by `close()`.

## 5. The Writer

`format_row` renders one record and does **not** append a terminator;
`format_csv` appends `lineterminator` after every record, including the last.
Fields are rendered first — `None` becomes the empty string, any other non-`str`
becomes `str(value)` — then quoted by wrapping in quotechars with every embedded
quotechar doubled.

| `quoting` | Policy |
|---|---|
| `QUOTE_MINIMAL` | Quote a field only if it contains `D`, `Q`, CR or LF, or if it is the sole field of the record and empty |
| `QUOTE_ALL` | Quote every field |
| `QUOTE_NONE` | Never quote; raise `CsvError` if a field contains `D`, `Q`, CR or LF |

`QUOTE_NONE` never quotes, so it does not apply the sole-empty-field rule below
and consequently does not round-trip `['']`. The round-trip guarantee of §7.4
covers `QUOTE_MINIMAL` and `QUOTE_ALL` only.

The sole-empty-field clause of `QUOTE_MINIMAL` exists for §3.2: `['']` written
unquoted would be an empty line and would read back as `[]`, so it is written
`""`. The empty record `[]` correctly writes as an empty line. Whitespace is
never stripped, added or normalised on either side: ` a ` is a three-character
field on read and is written back unquoted as ` a `.

## 6. Complexity

| Operation | Time | Space |
|---|---|---|
| `feed(chunk)` | O(len(chunk)) — each character examined once | O(1) beyond the pending record |
| `parse_csv(text)` | O(len(text)) | O(size of output) |
| `format_row(row)` | O(total field length) | O(total field length) |

The reader must not re-scan: `text = text[1:]` in a loop, or searching for a
closing quote from the document start, is O(n²) and violates the single pass.

## 7. Correctness Properties

1. **Field count** — a record has one more field than the delimiters consumed in
   states `START`, `UNQUOTED` and `AFTER_QUOTE`; delimiters consumed in `QUOTED`
   do not split fields.
2. **Quoted transparency** — for every string `s`, including ones containing
   delimiters, CR, LF and quotechars, `parse_csv(Q + s.replace(Q, QQ) + Q)` is `[[s]]`.
3. **Chunk invariance** — for any partition of `text` into chunks, the `feed`
   results concatenated with `close` equal `parse_csv(text)`. This must hold for
   single-character chunks and for boundaries splitting a CRLF or a `""` escape.
4. **Round trip** — for any list of records of strings `rows`,
   `parse_csv(format_csv(rows))` equals `rows`, under `QUOTE_MINIMAL` and `QUOTE_ALL`.
5. **Writer idempotence** — `format_csv(parse_csv(format_csv(rows)))` equals
   `format_csv(rows)`.
6. **Determinism and reuse** — parsing the same text twice yields equal results,
   and a reader returning from `close()` is back in its initial state, so it
   parses a second document exactly as a fresh reader would.
7. **Dialect agnosticism** — records read back from `format_csv(rows, delimiter=X,
   quotechar=Y)` are independent of `X` and `Y`; neither may be hard-coded.

## 8. Failure Modes and Edge Cases

- Empty input: `parse_csv("")` is `[]`, and `format_csv([])` is `""`.
- Terminators only: `"\r\n\r\n"` is `[[], []]`; `",,"` is `[['', '', '']]` —
  three empty fields, not one.
- A quoted field at end of input with no closing quote raises `CsvError`.
- A quotechar equal to the delimiter, a multi-character delimiter, or a delimiter
  of CR or LF raises `CsvError` at construction, before any input is consumed.
- A `quoting` value outside the three named modes raises `CsvError`.
- A non-`str` document, or a record that is a `str` rather than a sequence of
  fields, raises `CsvError` rather than being iterated character by character.
- `QUOTE_NONE` with a field that requires quoting raises `CsvError` rather than
  silently emitting unreadable output.
- Documents ending mid-record are handled by §3.2, not by dropping the record.

## 9. Public API

```python
QUOTE_MINIMAL: str = "minimal"
QUOTE_ALL: str = "all"
QUOTE_NONE: str = "none"

class CsvError(ValueError):
    """Every error raised by this library is a CsvError."""

class CsvReader:
    def __init__(self, *, delimiter: str = ",", quotechar: str = '"',
                 strict: bool = False) -> None: ...
    def feed(self, chunk: str) -> list[list[str]]:
        """Records completed by this chunk; a partial record is retained."""
    def close(self) -> list[list[str]]:
        """The pending record, if any. Raises CsvError inside a quoted field."""

def parse_csv(text: str, *, delimiter: str = ",", quotechar: str = '"',
              strict: bool = False) -> list[list[str]]: ...

def needs_quoting(field: str, *, delimiter: str = ",",
                  quotechar: str = '"') -> bool:
    """True if the field contains the delimiter, the quotechar, CR or LF."""

def format_row(row: Sequence[Any], *, delimiter: str = ",", quotechar: str = '"',
               quoting: str = QUOTE_MINIMAL) -> str:
    """One record, without a line terminator."""

def format_csv(rows: Iterable[Sequence[Any]], *, delimiter: str = ",",
               quotechar: str = '"', quoting: str = QUOTE_MINIMAL,
               lineterminator: str = "\r\n") -> str: ...
```

## 10. Implementation Notes

- **Do not import or delegate to the `csv` module (nor `_csv`, nor `pandas`).**
  The character-level state machine is the entire deliverable; a wrapper around
  `csv.reader` would satisfy every functional test while implementing nothing.
  Do not name the module `csv.py` either — it would shadow the stdlib module on
  `sys.path` for every other importer in the process.
- **Do not use `re`.** Once embedded newlines are allowed the grammar is
  context-sensitive across record boundaries; a regex scanner is not the design.
- **Do not split the input.** `str.split`, `str.rsplit` and `str.splitlines`
  cannot honour quoted delimiters or quoted newlines; the reader must consume
  characters one at a time. `str.replace` and `str.join` remain available to the
  *writer*, where quoting is a pure per-field transformation.
- Accumulate field text in a list and `"".join` it once per field rather than
  concatenating per character; validate the dialect in the constructor, not per
  character.
