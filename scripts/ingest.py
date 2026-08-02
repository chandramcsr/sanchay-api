"""
Sanchay docs RAG ingestion pipeline.

Pipeline: doc file -> split by headers -> sub-split by size -> embed -> insert into Postgres.

Run:
    pip install sentence-transformers psycopg2-binary pgvector --break-system-packages
    export DATABASE_URL="postgresql://..."   # your Neon connection string
    python ingest.py path/to/README.md path/to/privacy.html
"""

import os
import re
import sys
from dataclasses import dataclass, field

import psycopg2
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# CONCEPT: the embedding model
# ---------------------------------------------------------------------------
# We load bge-small-en-v1.5 once at startup. Loading is the "slow" part
# (downloads weights on first run, then loads into memory) — encoding after
# that is fast, even on CPU, because the model is small (~130MB).
#
# bge-small was trained specifically to produce embeddings where "similar
# meaning" = "small cosine distance". That's a different training objective
# than a general chat LLM, which is why we use a dedicated embedding model
# rather than, say, asking an LLM to describe similarity.
EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384  # must match the VECTOR(384) column in doc_chunks

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        print(f"Loading embedding model {EMBED_MODEL_NAME} (first run downloads weights)...")
        _model = SentenceTransformer(EMBED_MODEL_NAME)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Turn a batch of text chunks into embedding vectors.

    Batching matters: encoding 50 chunks in one call is much faster than
    50 separate calls, because the model can vectorize the batch internally
    instead of paying fixed overhead per call.
    """
    model = get_model()
    # bge models recommend NOT prefixing document text (only queries get a
    # prefix at retrieval time — see retrieve.py). Normalizing embeddings
    # lets us use cosine distance cleanly.
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return vectors.tolist()


# ---------------------------------------------------------------------------
# CONCEPT: structure-aware chunking
# ---------------------------------------------------------------------------
# Naive chunking (every N characters) can slice a sentence, a code block, or
# an idea in half. We chunk in two passes instead:
#   1. Split on markdown headers first, so each section stays topically
#      coherent and we can attach a "header path" as metadata.
#   2. Within a section, if it's still too big, sub-split by paragraph up to
#      a target size, with overlap so boundary content isn't lost entirely
#      to whichever side of the cut it landed on.


@dataclass
class Chunk:
    content: str
    header_path: str
    chunk_index: int = 0


HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

TARGET_CHARS = 1600      # ~400 tokens at ~4 chars/token, a reasonable rule of thumb
OVERLAP_CHARS = 200       # ~50 tokens of overlap between adjacent sub-chunks


def split_by_headers(text: str) -> list[tuple[str, str]]:
    """Split markdown into (header_path, section_text) pairs.

    We track a running stack of headers by level so a paragraph under
    "## Settings" > "### Recurring Expenses" gets the header_path
    "Settings > Recurring Expenses" — useful both for citations in the UI
    and as a light relevance signal later.
    """
    matches = list(HEADER_RE.finditer(text))
    if not matches:
        return [("", text.strip())] if text.strip() else []

    sections = []
    stack: list[tuple[int, str]] = []  # (level, title)

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        # maintain header stack: pop deeper/equal levels, push this one
        stack = [h for h in stack if h[0] < level]
        stack.append((level, title))
        header_path = " > ".join(t for _, t in stack)

        if body:
            sections.append((header_path, body))

    return sections


def sub_split(text: str, target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split a section into overlapping pieces if it exceeds target size.

    We split on paragraph boundaries (blank lines) rather than mid-sentence,
    accumulating paragraphs until we'd exceed target, then starting the next
    chunk `overlap` characters back from the cut point.
    """
    if len(text) <= target:
        return [text]

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    # Fallback: a paragraph with no internal blank lines can still be huge
    # (e.g. a long run-on doc section). Break those on sentence boundaries
    # so we never emit a single chunk far larger than target.
    expanded: list[str] = []
    for para in paragraphs:
        if len(para) <= target:
            expanded.append(para)
        else:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            expanded.extend(s for s in sentences if s.strip())
    paragraphs = expanded

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if current and len(current) + len(para) + 2 > target:
            chunks.append(current.strip())
            # start next chunk with the tail of the previous one, for overlap
            tail = current[-overlap:] if len(current) > overlap else current
            current = tail + "\n\n" + para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def chunk_document(text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    idx = 0
    for header_path, section_text in split_by_headers(text):
        for piece in sub_split(section_text):
            chunks.append(Chunk(content=piece, header_path=header_path, chunk_index=idx))
            idx += 1
    return chunks


# ---------------------------------------------------------------------------
# Loading + stripping HTML docs down to plain-ish markdown-like text
# ---------------------------------------------------------------------------


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if path.endswith(".html"):
        # crude but effective for a privacy-policy-style doc: strip tags,
        # keep line breaks around headings so split_by_headers still works.
        raw = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", lambda m: f"\n{'#' * int(m.group(1))} {m.group(2)}\n", raw, flags=re.S)
        raw = re.sub(r"<[^>]+>", "", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw


# ---------------------------------------------------------------------------
# DB insert
# ---------------------------------------------------------------------------


def ingest_file(path: str, conn) -> int:
    text = load_text(path)
    chunks = chunk_document(text)
    if not chunks:
        print(f"  no content extracted from {path}")
        return 0

    vectors = embed([c.content for c in chunks])

    source = os.path.basename(path)
    with conn.cursor() as cur:
        # CONCEPT: re-ingestion. If you run this again after editing a doc,
        # you don't want duplicate stale chunks piling up — so we delete
        # this source's existing chunks first, then insert fresh ones.
        cur.execute("DELETE FROM doc_chunks WHERE source = %s", (source,))
        for chunk, vector in zip(chunks, vectors):
            cur.execute(
                """
                INSERT INTO doc_chunks (source, chunk_index, content, embedding)
                VALUES (%s, %s, %s, %s)
                """,
                (source, chunk.chunk_index, f"{chunk.header_path}\n\n{chunk.content}".strip(), vector),
            )
    conn.commit()
    print(f"  inserted {len(chunks)} chunks from {source}")
    return len(chunks)


def main():
    if len(sys.argv) < 2:
        print("Usage: python ingest.py <file1> [file2 ...]")
        sys.exit(1)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("Set DATABASE_URL to your Neon connection string")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    # Without this, psycopg2 has no adapter for a Python list -> pgvector's
    # `vector` type, and every insert below fails. Must be called on this
    # connection before any query that touches the embedding column.
    register_vector(conn)
    total = 0
    try:
        for path in sys.argv[1:]:
            print(f"Ingesting {path}...")
            total += ingest_file(path, conn)
    finally:
        conn.close()

    print(f"Done. {total} chunks ingested.")


if __name__ == "__main__":
    main()
