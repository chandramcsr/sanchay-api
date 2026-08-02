"""
Sanchay docs RAG retrieval.

Pipeline: user question -> query embedding -> pgvector cosine similarity
search -> assemble top chunks into a context string within a size budget.

Run:
    pip install sentence-transformers psycopg2-binary pgvector --break-system-packages
    export DATABASE_URL="postgresql://..."   # your Neon connection string
    python retrieve.py "how do I set up cloud sync?"
"""

import os
import sys
from dataclasses import dataclass

import psycopg2
from pgvector.psycopg2 import register_vector

# Reuses ingest.py's model loader rather than duplicating it -- same
# model, same load-once behavior, one place that knows the model name.
from ingest import get_model

# ---------------------------------------------------------------------------
# CONCEPT: the query-side instruction prefix
# ---------------------------------------------------------------------------
# bge-small-en-v1.5's own docs are explicit that documents should NOT get an
# instruction prefix (ingest.py doesn't add one), but short queries
# retrieving long documents benefit from one -- exactly our shape here
# (a one-line question retrieving paragraph-sized doc chunks). This is the
# exact string BAAI recommends; changing the wording changes what the model
# was tuned against, so it's not just descriptive text.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# How many chunks to pull back and how much total text to allow into the
# context. TOP_K is generous on purpose (over-fetch, then trim by budget)
# since a few chunks can be near TARGET_CHARS each and still leave room.
# MAX_CONTEXT_CHARS is deliberately a hard ceiling independent of TOP_K --
# grabbing 5 chunks doesn't help if it means 8000+ characters land in a
# prompt uncounted. ~6000 chars is roughly 1500 tokens, a reasonable slice
# of a prompt budget for a doc-grounded answer, not the whole thing.
TOP_K = 5
MAX_CONTEXT_CHARS = 6000


@dataclass
class RetrievedChunk:
    source: str
    chunk_index: int
    content: str
    distance: float  # cosine distance -- lower is more similar


def embed_query(text: str) -> list[float]:
    model = get_model()
    vector = model.encode([QUERY_PREFIX + text], normalize_embeddings=True)
    return vector[0].tolist()


def search(query: str, conn, top_k: int = TOP_K) -> list[RetrievedChunk]:
    """Cosine-distance nearest neighbor search against doc_chunks.

    Uses <=> (cosine distance) to match ingest.py's own reasoning for
    normalizing embeddings at insert time -- keeping insert-side and
    query-side on the same distance metric matters, mixing <=> and <->
    (Euclidean) against normalized vectors gives a different ranking.

    No hard similarity-score cutoff here on purpose: BAAI's own docs for
    this model are explicit that the absolute cosine value isn't
    meaningful for filtering (their finetuning temperature skews the
    whole distribution into a narrow band) -- what's meaningful is
    relative order, which top_k ranking already gives us. A fixed
    threshold would silently drop or admit results based on a number
    that isn't calibrated to mean what a threshold implies.
    """
    query_vector = embed_query(query)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source, chunk_index, content, embedding <=> %s AS distance
            FROM doc_chunks
            ORDER BY distance
            LIMIT %s
            """,
            (query_vector, top_k),
        )
        rows = cur.fetchall()
    return [RetrievedChunk(source=r[0], chunk_index=r[1], content=r[2], distance=r[3]) for r in rows]


def assemble_context(chunks: list[RetrievedChunk], max_chars: int = MAX_CONTEXT_CHARS) -> tuple[str, list[str]]:
    """Concatenate chunks into one context string, stopping at the budget.

    Chunks arrive pre-sorted by relevance (closest first), so truncating
    at the budget means dropping the least relevant results first, not an
    arbitrary cut. Returns the assembled text plus which sources actually
    made it in, for citing what the answer was grounded in.
    """
    parts: list[str] = []
    sources: list[str] = []
    used = 0

    for chunk in chunks:
        piece = chunk.content.strip()
        # +2 accounts for the blank-line separator joined in below
        if used + len(piece) + 2 > max_chars:
            if not parts:
                # First chunk alone exceeds the budget -- include it
                # truncated rather than return an empty context.
                parts.append(piece[: max_chars - used])
                sources.append(chunk.source)
            break
        parts.append(piece)
        sources.append(chunk.source)
        used += len(piece) + 2

    return "\n\n".join(parts), sources


def retrieve(query: str, conn, top_k: int = TOP_K, max_context_chars: int = MAX_CONTEXT_CHARS) -> tuple[str, list[str]]:
    chunks = search(query, conn, top_k=top_k)
    return assemble_context(chunks, max_chars=max_context_chars)


def main():
    if len(sys.argv) < 2:
        print('Usage: python retrieve.py "your question"')
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("Set DATABASE_URL to your Neon connection string")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    register_vector(conn)
    try:
        chunks = search(query, conn)
        print(f"Top {len(chunks)} chunks for: {query!r}\n")
        for c in chunks:
            print(f"[{c.source} #{c.chunk_index}] distance={c.distance:.4f}")
            print(c.content[:200].replace("\n", " ") + ("..." if len(c.content) > 200 else ""))
            print()

        context, sources = assemble_context(chunks)
        print(f"--- Assembled context: {len(context)} chars from {sources} ---")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
