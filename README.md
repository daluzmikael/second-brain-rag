# Second Brain

A search-and-answer bot over an Obsidian vault. Ask it questions in plain English
and it answers only from your own notes, citing the note each claim came from.

Everything except the answer generation runs on your machine. No database
server, no Docker, and search never leaves the laptop.

```
open ~/Applications/SecondBrain.app     # menu bar icon; Option+Space to ask
```

## Setup

1. Put your key and vault path in `.env` (copy `.env.example` if missing):

   ```
   OPENAI_API_KEY=sk-...
   VAULT_PATH="~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MiksVaultyBaulty"
   ```

   Only answer generation uses the key. Indexing and search are local.

   An iCloud-synced Obsidian vault lives under
   `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/<vault>`. Quote the
   path — it contains spaces. A vault kept anywhere else works just as well;
   point `VAULT_PATH` at the folder holding your notes.

   Moving the vault later costs nothing: the index keys on each note's relative
   path and content hash, not its absolute location, so a move re-indexes only
   the notes that genuinely changed.

2. Build the index:

   ```
   ./brain index
   ```

3. Build and open the menu bar app:

   ```
   ./mac/build.sh
   open ~/Applications/SecondBrain.app
   ```

   macOS will ask once for access to your Documents folder. To have it there
   every day: System Settings › General › Login Items › **+** › SecondBrain.

The app starts the Python server itself and shuts it down when you quit. It also
re-scans your vault every 30 seconds, so notes you edit in Obsidian become
answerable without you running anything.

Prefer a terminal? `./brain chat`, or `./brain serve` for the same UI in a
browser tab.

## Using it

| | |
| --- | --- |
| **Option+Space** | open the chat from anywhere |
| Click the menu bar icon | same thing |
| Right-click the icon | open in browser, reload, reindex now, quit |
| Enter / Shift+Enter | send / newline |
| "New chat" | forget the current conversation |

Nothing is saved. Closing the popover keeps the conversation; "New chat" or
quitting discards it.

## Commands

| Command | What it does |
| --- | --- |
| `./brain index` | Index new and edited notes. Unchanged notes are never opened. |
| `./brain index --rebuild` | Throw the index away and start over. |
| `./brain chat` | Terminal chat. `/new` `/sources` `/status` `/reindex` `/help` `/exit`. |
| `./brain ask "question"` | One question, one answer. |
| `./brain search "terms"` | Retrieval only, no model call. Shows which signal matched. |
| `./brain serve` | The web UI, in a browser. |
| `./brain status` | Vault, index and model info. |
| `./brain service …` | launchd agent — see the note below before using. |

## Speed

Measured on this vault (170 notes, 439 chunks, ~54k tokens):

| | |
| --- | --- |
| Vector search over the whole vault | **18 µs** |
| Full retrieval (embed + keyword + fusion + assembly) | **23–280 ms** |
| Embedding one query, locally | 0.1 ms |
| Embedding one query, via OpenAI | ~800 ms |
| Indexing the entire vault, locally | ~2.5 s |
| First token of an answer | 0.7–3 s |

Retrieval is not the bottleneck and cannot meaningfully be made faster; what
remains is OpenAI generating the answer. Two things keep it that way:

**Embeddings run locally.** A static [model2vec](https://github.com/MinishLab/model2vec)
model (`potion-retrieval-32M`, ~30 MB, no PyTorch) replaced the OpenAI embedding
call. On a 22-query benchmark against this vault the two were *tied* at 21/21
top-1 — the hosted model's extra nuance is swamped by the keyword and title
signals — while the local one is ~85× faster, free, and works offline. Set
`EMBEDDING_BACKEND=openai` in `.env` to switch back.

**The server stays warm.** Python's import cost is ~10s on a cold start, against
milliseconds for the search itself, so the process is kept resident rather than
paying that per question.

## How retrieval works

Notes in this vault are short, and many name their subject only in the filename:
`Japan.md` contains the words "Tokyo" and "Dotonbori" and nothing else. Retrieval
is built around that.

**Chunking follows the document structure.** Notes are split on markdown headings
rather than by a fixed token window, so a `###` section and the table under it
stay together. Tables are never cut across rows unless a single table exceeds the
budget, and then the header row is repeated in each piece. Obsidian's cell padding
is stripped first — some tables here had 3000-character rows that were almost
entirely spaces.

**Every chunk is embedded with its context.** A bare table of months means nothing
on its own, so each chunk is embedded with a header naming its note, folder,
heading path and tags. That is what makes "seasonal structure for my volleyball
league" match a table whose cells never say "volleyball".

**Three signals are fused.** Semantic search, keyword search (SQLite FTS5 with
BM25 and porter stemming, which catches exact terms like `NAVC`), and a
title/folder/tag match. Rankings are combined with Reciprocal Rank Fusion, and
the title signal is applied once per *note* rather than once per chunk, so a long
note cannot win by accumulating many weak matches.

**Context is assembled per note, not per chunk.** The median note is a couple of
hundred tokens, so whole notes are handed to the model wherever they fit within
the budget. Longer notes fall back to their matching sections. The model never
sees half a table.

**Placeholder notes answer honestly.** A note that is empty, or that contains
only a heading (`# Iceland`), is indexed as such — so "what did I write about
Iceland?" reports that the note exists and is blank, instead of reciting the
heading back.

## A note on launchd and macOS permissions

`./brain service install` registers a background agent that starts at login. It
refuses to install when the project or vault sits in `~/Documents`, `~/Desktop`,
`~/Downloads` or iCloud Drive — macOS blocks launchd agents from those folders
entirely, and every read fails with "Operation not permitted" (which surfaces as
confusing errors like `EDEADLK` mid-import).

That is why the menu bar app runs the server itself: a GUI app is granted access
by the user, and its child processes inherit the grant. Use the app unless your
vault lives somewhere unprotected.

## Layout

```
app/
  config.py       settings and tuning knobs, all overridable in .env
  vault.py        walk the vault; frontmatter, wikilinks, tags, table cleanup
  chunking.py     heading-aware markdown chunking
  embeddings.py   local / openai / offline-test embedding backends
  store.py        SQLite index: notes, chunks, vectors, FTS5
  index.py        incremental build/refresh
  search.py       hybrid retrieval and note-level context assembly
  synthesize.py   prompts, follow-up query rewriting, answer streaming
  bot.py          rewrite -> retrieve -> answer
  chat.py         terminal chat
  api.py          FastAPI server, SSE streaming, vault watcher
  service.py      launchd agent management
  web/index.html  the chat UI, self-contained
  cli.py          command line entry point
mac/
  SecondBrain.swift   menu bar app: status item, hotkey, web view, server process
  build.sh            compiles and installs SecondBrain.app (no Xcode needed)
data/index.sqlite3    the index (gitignored, rebuildable)
```

## Other behaviour

- **No chat history is stored.** The browser holds the current conversation and
  sends it with each request.
- **Follow-ups are resolved before searching.** "What about the awards?" is
  rewritten into a standalone query using the conversation, then searched.
- **Citations are clickable.** Source chips open the note in Obsidian.
- **Retrieval can be tested without any model.** `FAKE_EMBEDDINGS=1 ./brain index`
  uses deterministic local hashes, so the pipeline works with no key and no
  network. Rebuild afterwards.

Tuning knobs (chunk size, context budget, signal weights, score floor, watcher
interval) are listed in `.env.example`.
