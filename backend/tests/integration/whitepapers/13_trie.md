# Prefix Trie with Wildcard Matching, Longest-Prefix Lookup, and Pruning Deletion

Python Library Specification

## Abstract

This document specifies a Python library implementing a prefix trie mapping
string keys to arbitrary values: exact lookup, prefix enumeration, O(1) prefix
cardinality via per-node subtree counters, longest-prefix lookup, glob-style
wildcard matching performed *during* traversal rather than by filtering
enumerated keys, and deletion that prunes exactly the nodes made redundant — no
more and no fewer.

## 1. Overview and Design Rationale

Every node of a trie corresponds to exactly one string: the concatenation of the
edge labels from the root, which itself corresponds to the empty string. The node
set is therefore precisely the set of prefixes of the stored keys, which is what
§9 restates and what makes both classes of deletion bug visible:

- **Orphaning (under-pruning)** — deleting a key leaves the chain of nodes that
  spelled it: unreachable as keys, still consuming space, and still making
  `starts_with` answer True for a prefix nothing stores.
- **Over-pruning** — deleting a key removes nodes another key still needs,
  silently destroying unrelated entries on a shared branch.

These are opposite errors, so a suite that only checks "the deleted key is gone"
catches neither.

## 2. Data Structure and Invariants

Each node holds `children` (a mapping from a single character to a child node),
`terminal` (True if the string spelled by this node is a stored key), `value`
(meaningful only when `terminal`), and `size` (stored keys in this node's subtree,
including itself). `terminal` must not be inferred from `value is not None`,
because `None` is a legal value (§10). A node is **live** if it is terminal or has
at least one child, and **dead** otherwise; the root is always live and is never
removed. After every public operation:

1. Every non-root node is live. A dead non-root node is the orphan of §1.
2. `node.size == (1 if node.terminal else 0) + sum(c.size for c in node.children.values())`.
3. `len(trie) == root.size`, and every edge label has length exactly 1.
4. `node_count()` equals the number of nodes reachable from the root, and equals
   `len({k[:i] for k in trie.keys() for i in range(len(k) + 1)} | {""})`.

## 3. Insertion

`insert(key, value)` validates the key (§7) before touching anything, then
descends it character by character, creating each missing child node and counting
the creation. If the final node is already terminal, its value is replaced and the
call returns False, leaving every `size` counter alone — bumping counters on a
replacement is a common error that `count_prefix` exposes. Otherwise the node is
marked terminal, `size` is incremented on every node of the root-to-node path
(each gains one key), and the call returns True.

## 4. Lookup, Enumeration, and Longest Prefix

- `get(key, default)` descends the path and returns `node.value` if the final node
  exists and is terminal, else `default`. `contains` is the same walk sans value.
- `starts_with(prefix)` is True iff the path for `prefix` exists; that node need
  not be terminal.
- `count_prefix(prefix)` returns the prefix node's `size`, or 0 if the path does
  not exist. It must be O(len(prefix)) — counting the matches is not acceptable.
- `keys(prefix)` and `items(prefix)` return **lists in ascending lexicographic
  order**, restricted to keys carrying that prefix (`prefix=""` returns
  everything). Order must not depend on insertion order, so children are visited
  in sorted character order; iterating a `dict` of children in insertion order is
  not sufficient. Emitting a node before its descendants then gives exactly
  lexicographic order, since a string sorts before every string extending it.
- `longest_prefix(query)` walks from the root remembering the last terminal node
  reached and returns that `(key, value)` pair, or `None` if no stored key is a
  prefix of `query`. Candidates are totally ordered by length, so no tie-break is
  needed. `query` counts as a prefix of itself: if stored, it is the answer. The
  empty key, if stored, is a prefix of every query and so is the fallback answer
  rather than `None`.

## 5. Wildcard Matching

`match(pattern)` returns the sorted stored keys matching `pattern`, where `?` is
exactly one character, `*` is zero or more, and every other character is itself —
shell-glob semantics without character classes: no escapes, no `[...]`, no
anchors, and the pattern always matches the whole key.

```
walk(node, pattern, i, prefix, out):
    if i == len(pattern):
        if node.terminal: out.add(prefix)
    elif pattern[i] == '*':
        walk(node, pattern, i + 1, prefix, out)                 # consume zero
        for ch, child in node.children: walk(child, pattern, i, prefix + ch, out)
    elif pattern[i] == '?':
        for ch, child in node.children: walk(child, pattern, i + 1, prefix + ch, out)
    elif pattern[i] in node.children:
        walk(node.children[pattern[i]], pattern, i + 1, prefix + pattern[i], out)
```

Matching descends the trie, so literal segments prune the search immediately.
**Duplicates:** the two `*` branches can reach one key by different routes, so
adjacent stars (`**`, or `*a*` against `"aa"`) reach some keys twice and appending
to a list yields duplicates. Results must be de-duplicated — accumulate into a
set, or collapse runs of `*` first — and returned sorted ascending.

## 6. Deletion and Pruning

```
delete(key):
    walk down key recording each (parent, char); if a child is missing return False
    if not node.terminal: return False        # a prefix of a key is not a key
    node.terminal, node.value = False, None
    node.size -= 1
    for (parent, ch) in reversed(recorded path):
        child = parent.children[ch]
        if not child.terminal and not child.children:
            del parent.children[ch]; node_count -= 1
        parent.size -= 1
    return True
```

Pruning unwinds bottom-up and stops at the first live ancestor, because a live
node fails the `not terminal and not children` test and every node above it then
has at least one child. The root never appears as a `child` here, so it survives.
With `{"car": 1, "cart": 2}` the trie has 5 nodes — root, `c`, `ca`, `car`,
`cart` — giving the three cases that matter:

- `delete("cart")` must leave **4** nodes: the `t` node is dead and goes, while
  `car` is terminal so pruning stops there. Leaving 5 is orphaning.
- `delete("car")` must leave **5** nodes and `"cart" -> 2` intact: the `car` node
  stops being terminal but still has a child, so nothing is pruned. Removing it
  here is the over-pruning error and destroys `"cart"`.
- `delete("ca")` returns False and changes nothing — `ca` is a prefix, not a key.
  An implementation that prunes on a failed delete corrupts the trie.

The formulations above are written recursively for clarity, but the path
operations (`insert`, `get`, `contains`, `delete`, `starts_with`, `count_prefix`,
`longest_prefix`) must use an explicit loop or path stack: their natural recursion
depth is the key length while Python's default limit is 1000, and keys of length
up to 5000 must work. Enumeration (`keys`, `items`, `match`) may recurse and need
only support tries of height 100.

## 7. Validation

- A non-`str` key raises `TypeError`; a non-`str` pattern raises `TypeError`;
  `from_keys` raises `TypeError` if its argument is not iterable.
- A **stored** key containing `*` or `?` raises `ValueError` — it could never be
  addressed unambiguously by `match`. This applies to `insert` and `from_keys`
  only; lookup arguments are compared literally, so `get("a*b")` simply misses.
- Validation precedes mutation: a rejected `insert` leaves `len`, `node_count`,
  and every stored value untouched.

## 8. Complexity

`m` = key length, `p` = prefix length, `N` = number of nodes.

| Operation | Time | Space |
|---|---|---|
| insert / get / contains / delete | O(m) | O(m) path stack |
| starts_with / count_prefix / longest_prefix | O(p) | O(1) |
| keys / items | O(p + total length of results) | O(output) |
| match | O(N) worst case | O(output) |
| `__len__` / node_count | O(1) | O(1) |

N is bounded by the number of distinct prefixes of the stored keys, never by their
summed length when they share branches.

## 9. Correctness Properties

1. **Value fidelity** — after `insert(k, v)`, `get(k) == v` and `k in trie`;
   re-inserting `k` replaces the value and changes neither `len` nor `node_count`.
2. **Node exactness** — invariant 4 of §2 holds after every operation.
3. **Deletion locality** — `delete(k)` removes `k` and changes no other key's
   presence or value.
4. **Pruning exactness** — after `delete(k)` no dead non-root node remains, and
   every node on another key's path survives (§6).
5. **Churn round trip** — insertions followed by deletion of exactly those keys
   return `len`, `node_count`, and `items()` to their prior values: structure is a
   function of content, not of history.
6. **Enumeration order** — `keys(prefix)` is sorted ascending, independent of
   insertion order.
7. **Counter consistency** — `count_prefix(p) == len(keys(p))` for every `p`, and
   `count_prefix("") == len(trie)`.
8. **Longest prefix** — the returned key is stored, is a prefix of the query, and
   no longer stored key is a prefix of the query.
9. **Wildcard exactness** — `match(pattern)` is the sorted list of stored keys
   matching §5, with no duplicates and no omissions.
10. **Failed operations are inert** — a delete of an absent key and a rejected
    insert leave `node_count` and every stored value unchanged.

## 10. Failure Modes and Edge Cases

- The empty string is a legal key: it makes the root terminal, contributes no
  nodes (`node_count()` stays 1 for a trie holding only `""`), and must survive
  `delete("")` without the root being pruned.
- A key that is a strict prefix of another, and one that strictly extends another,
  must each be independently insertable and deletable (§6).
- Deleting an absent key, or a prefix that is not a key, returns False and leaves
  the trie identical. Re-inserting a deleted key restores the earlier state.
- `None` as a stored value: `get(k)` returns `None` for a stored `None` and the
  supplied default for a miss; `contains` distinguishes the two.
- `match("*")` matches every stored key including `""`; `match("")` matches only
  the empty key; a pattern with no wildcards behaves as an exact lookup.
- A 5000-character key must insert, look up, and delete without `RecursionError`,
  and deleting it must return `node_count()` to 1.
- `clear()` returns the trie to a single root node: `len == 0`, `node_count() == 1`.

## 11. Public API

```python
WILDCARD_ANY: str = "*"   # zero or more characters
WILDCARD_ONE: str = "?"   # exactly one character

class Trie:
    def __init__(self) -> None: ...
    def insert(self, key: str, value: Any = None) -> bool: ...
    def get(self, key: str, default: Any = None) -> Any: ...
    def contains(self, key: str) -> bool: ...
    def delete(self, key: str) -> bool: ...
    def clear(self) -> None: ...
    def starts_with(self, prefix: str) -> bool: ...
    def count_prefix(self, prefix: str) -> int: ...
    def keys(self, prefix: str = "") -> list[str]: ...
    def items(self, prefix: str = "") -> list[tuple[str, Any]]: ...
    def longest_prefix(self, query: str) -> tuple[str, Any] | None: ...
    def match(self, pattern: str) -> list[str]: ...
    def node_count(self) -> int: ...
    def __len__(self) -> int: ...
    def __contains__(self, key: str) -> bool: ...

def from_keys(keys: Iterable[str]) -> Trie: ...   # each key stored with value None
```

## 12. Implementation Notes

- Do not delegate to any third-party trie or prefix-tree package — `pygtrie`,
  `pytrie`, `marisa_trie`, `datrie`, `hat_trie`, `patricia_trie`,
  `pyahocorasick`/`ahocorasick` or any equivalent. The explicit node structure and
  the pruning deletion of §6 are the deliverable.
- Wildcard matching must descend the trie as in §5. Do not import `re`, `regex`,
  `fnmatch`, or `glob`: enumerating every key and filtering it with a compiled
  pattern discards the data structure this specification exists to build.
- `str.startswith` must not appear in the implementation. Prefix questions are
  answered by descending child pointers; a `startswith` call is evidence that keys
  are being scanned linearly, violating §8.
- Node removal belongs in exactly one place — the unwind loop of §6 — so the
  pruning rule has a single definition, and `node_count()` is maintained there
  rather than recomputed by traversal (§8 requires O(1)).
