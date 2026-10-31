---
title: JSON and full-text search in SQLite
author: MOZGII, Inc.
lang: en
---

# JSON and full-text search in SQLite

How to store and query JSON documents in SQLite from Python, index fields inside them, and search text with FTS5: tables kept in sync by triggers, ranking with bm25, snippets, and query strings that cannot break the parser.

## Contents

This reference has 8 sections.

1. JSON columns and the JSON functions
2. Reading fields with -> and ->>
3. Indexing a JSON field
4. Expanding arrays with json_each
5. FTS5 full-text tables
6. Keeping an FTS5 index in sync with triggers
7. Ranking, snippets and highlights
8. Safe FTS5 query strings

## 1. JSON columns and the JSON functions

SQLite stores JSON as ordinary text and processes it with built-in JSON functions (part of the core since SQLite 3.38). Write the document with `json.dumps` and let SQL read fields from it.

```python
import json
import sqlite3

con = sqlite3.connect(":memory:", autocommit=True)
con.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, doc TEXT NOT NULL CHECK (json_valid(doc))) STRICT")
docs = [
    {"name": "lamp", "price": 30, "tags": ["light", "desk"], "dims": {"h": 40}},
    {"name": "chair", "price": 120, "tags": ["seat"], "dims": {"h": 90}},
    {"name": "desk", "price": 300, "tags": ["desk", "wood"], "dims": {"h": 75}},
]
con.executemany("INSERT INTO item (doc) VALUES (?)", [(json.dumps(d),) for d in docs])
con.execute("SELECT json_extract(doc, '$.dims.h') FROM item WHERE id = 2").fetchone()[0]   # 90
```

- The `CHECK (json_valid(doc))` constraint refuses broken JSON at insert time.
- `json_set`, `json_insert`, `json_replace` and `json_remove` edit a document in SQL: `UPDATE item SET doc = json_set(doc, '$.price', ?) WHERE id = ?`.
- SQLite 3.45+ also has JSONB, a binary form (`jsonb(...)`, `jsonb_extract`) that skips re-parsing; store it in a `BLOB` column when documents are large and read often.

## 2. Reading fields with -> and ->>

The `->>` operator returns an SQL value (text, integer, real); `->` returns JSON text. Use `->>` in comparisons and `ORDER BY`.

```python
rows = con.execute(
    "SELECT doc ->> '$.name' FROM item WHERE doc ->> '$.price' < ? ORDER BY doc ->> '$.price'", (200,)
).fetchall()
rows                                                # [('lamp',), ('chair',)]
con.execute("SELECT doc -> '$.dims' FROM item WHERE id = 1").fetchone()[0]   # '{"h":40}'
```

## 3. Indexing a JSON field

A query that filters on `doc ->> '$.price'` reads every row unless that expression is indexed. Index the exact expression, or add a generated column and index it.

```python
con.execute("ALTER TABLE item ADD COLUMN price INTEGER GENERATED ALWAYS AS (doc ->> '$.price') VIRTUAL")
con.execute("CREATE INDEX item_price ON item (price)")
plan = con.execute("EXPLAIN QUERY PLAN SELECT id FROM item WHERE price > 100").fetchall()
plan[0][3]                                          # 'SEARCH item USING INDEX item_price (price>?)'
```

- `EXPLAIN QUERY PLAN` shows `SCAN item` for a full scan and `SEARCH ... USING INDEX` when the index is used.
- A `VIRTUAL` generated column costs no storage; `STORED` columns cannot be added with `ALTER TABLE`.

## 4. Expanding arrays with json_each

`json_each` turns a JSON array into rows, so a "has this tag" filter is a join, not string matching.

```python
con.execute("""
    SELECT DISTINCT item.doc ->> '$.name'
    FROM item, json_each(item.doc, '$.tags') AS tag
    WHERE tag.value = ?
    ORDER BY 1
""", ("desk",)).fetchall()                          # [('desk',), ('lamp',)]
```

`json_group_array` and `json_group_object` go the other way: they aggregate rows into one JSON value, which is handy for returning a parent row with its children in one query.

## 5. FTS5 full-text tables

FTS5 is SQLite's full-text search engine: a virtual table with an inverted index over its text columns. Check that the build has it (`PRAGMA compile_options` lists `ENABLE_FTS5`); python.org, uv and most Linux builds do.

```python
con.execute("CREATE TABLE note (id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL)")
con.execute("""
    CREATE VIRTUAL TABLE note_fts USING fts5(
        title, body,
        content='note', content_rowid='id',          -- external content: the text lives only in note
        tokenize='unicode61 remove_diacritics 2'
    )
""")
```

- `content='note'` makes an external-content table: FTS5 stores only the index, and reads the text from `note` by rowid. It saves space, but you must keep the index in sync (section 6).
- `unicode61 remove_diacritics 2` folds case and accents, so "cafe" finds "Café". `tokenize='porter unicode61'` adds English stemming ("running" finds "run"); `trigram` supports substring and `LIKE` searches at the cost of a larger index.

## 6. Keeping an FTS5 index in sync with triggers

An external-content FTS5 table is updated by three triggers on the source table. An update deletes the old entry and inserts the new one, using the special `'delete'` command.

```python
con.executescript("""
CREATE TRIGGER note_ai AFTER INSERT ON note BEGIN
    INSERT INTO note_fts (rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER note_ad AFTER DELETE ON note BEGIN
    INSERT INTO note_fts (note_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER note_au AFTER UPDATE ON note BEGIN
    INSERT INTO note_fts (note_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO note_fts (rowid, title, body) VALUES (new.id, new.title, new.body);
END;
""")
con.executemany("INSERT INTO note (title, body) VALUES (?, ?)", [
    ("Café opening hours", "The café on the corner opens at eight."),
    ("Backup plan", "Run the SQLite backup every night and test a restore."),
    ("Search notes", "FTS5 ranks results with bm25; the backup note mentions restore."),
])
```

- The `'delete'` command must receive the old values exactly; otherwise the index keeps stale tokens.
- After a bulk load without triggers, or to repair an index, rebuild it: `INSERT INTO note_fts (note_fts) VALUES ('rebuild')`.
- `INSERT INTO note_fts (note_fts) VALUES ('optimize')` merges index segments after large changes.

## 7. Ranking, snippets and highlights

`MATCH` filters rows, the hidden `rank` column orders them by bm25 (lower is better), and `snippet()` and `highlight()` mark the matched words for display.

```python
hits = con.execute("""
    SELECT note.id, snippet(note_fts, 1, '[', ']', '...', 8) AS excerpt
    FROM note_fts JOIN note ON note.id = note_fts.rowid
    WHERE note_fts MATCH ?
    ORDER BY rank
""", ("backup",)).fetchall()
[h[0] for h in hits]                                # [2, 3]
con.execute("SELECT highlight(note_fts, 0, '<b>', '</b>') FROM note_fts WHERE note_fts MATCH ?", ("cafe",)).fetchone()[0]   # '<b>Café</b> opening hours'
```

- `snippet(table, column, open, close, ellipsis, max_tokens)`: column `-1` picks the best column.
- Weight columns in ranking with `bm25(note_fts, 10.0, 1.0)` (title ten times the body) in `ORDER BY`.
- Combine with ordinary filters in the same query (`AND note.archived = 0`); FTS5 runs first, then the join filters.

## 8. Safe FTS5 query strings

The text after `MATCH` is FTS5 query syntax: `AND`, `OR`, `NOT`, `NEAR`, `*` for prefixes, `column:` filters and double quotes for phrases. Raw user input such as `C++ "quoted` raises `fts5: syntax error`. Quote each word as a phrase before passing it, still as a parameter.

```python
def fts_query(user_text: str, prefix: bool = True) -> str:
    """Each word becomes a quoted phrase; all words must match; the last one may be a prefix."""
    words = [w.replace('"', '""') for w in user_text.split()]
    if not words:
        return '""'
    terms = [f'"{w}"' for w in words]
    if prefix:
        terms[-1] += "*"
    return " AND ".join(terms)


fts_query('C++ "rest')                              # '"C++" AND """rest"*'
con.execute("SELECT rowid FROM note_fts WHERE note_fts MATCH ?", (fts_query("rest"),)).fetchall()   # [(2,), (3,)]
```

- Offer the raw syntax only to people who ask for it (an "advanced search" box), and catch `sqlite3.OperationalError` there.
- An empty query `""` matches nothing; check for empty input before querying.
- The tokenizer decides what a "word" is: `unicode61` drops `+` and `"`, so `"C++"` searches for `c`. For code or identifiers, the `trigram` tokenizer works better.
