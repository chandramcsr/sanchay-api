"""
Embedding generation for Ask Sanchay's retrieval layer.

Uses the same model as this repo's existing docs RAG pipeline
(scripts/ingest.py/retrieve.py) rather than introducing a second one --
BAAI/bge-small-en-v1.5, so there's one embedding dependency to install
and cache, not two.

The model itself can't be exercised inside this development sandbox --
its dependency (torch) needs a CPU-only wheel from a package index this
sandbox's network policy doesn't allow reaching, and the sandbox ran out
of disk space pulling the full GPU/CUDA build from plain PyPI instead.
That's a constraint of this specific development environment, not of
the approach: Render's actual build has normal internet access.
embed_document()/embed_query() below are correct and will work there --
they just haven't been exercised live here. Everything that calls them
(rag_service.py, transaction_service.py) takes the embedding function as
a parameter specifically so the rest of the pipeline can be tested with
a stub in the meantime.
"""
from functools import lru_cache

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"

# bge-small-en-v1.5's own model card is explicit that documents should
# NOT get an instruction prefix, but short queries retrieving longer
# passages benefit from one -- exactly this shape (a one-line question
# retrieving transaction-sized passages). This is the exact string BAAI
# recommends; changing the wording changes what the model was tuned
# against, so it isn't just descriptive text. Matches
# scripts/retrieve.py's QUERY_PREFIX exactly, same reasoning.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _get_model():
    # Imported lazily, inside the function, not at module load time --
    # so importing this module (e.g. from tests that only need
    # transaction_to_text or cosine_similarity) never requires
    # sentence-transformers/torch to be installed at all. Cached so
    # the model loads once per process, not once per request.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def embed_document(text: str) -> list[float]:
    """
    Embeds a transaction (or any indexed passage) for storage -- no
    prefix, per bge-small's own guidance.
    """
    model = _get_model()
    return model.encode(text, normalize_embeddings=True).tolist()


def embed_query(text: str) -> list[float]:
    """
    Embeds a user's question for retrieval -- the QUERY_PREFIX is
    what makes this asymmetric: NON-NEGOTIABLE RULE (per the RAG
    playbook this was built against) is that mixing prefixed and
    unprefixed encodings, or using the wrong one, degrades recall
    measurably and silently -- there's no error, just worse retrieval
    that's hard to notice without an eval set. Two separate, clearly
    named functions (this one and embed_document(), not one embed_fn()
    with a mode flag) is the guard against that -- a flag is easy to
    get backwards at a call site; a wrong function name is not.
    """
    model = _get_model()
    return model.encode(QUERY_PREFIX + text, normalize_embeddings=True).tolist()


def get_document_embed_fn():
    """FastAPI dependency wrapping embed_document -- same pattern as
    get_db/get_sanchay_app_db (app/core/database.py): routers depend on
    this rather than importing embed_document directly, so tests can
    override it via app.dependency_overrides with a stub."""
    return embed_document


def get_query_embed_fn():
    """Same pattern, for the query side -- kept as a separate
    dependency (not one embed_fn dependency with a mode flag) so it's
    structurally impossible to accidentally wire the document embedder
    into the query path or vice versa."""
    return embed_query


def transaction_to_text(*, description: str | None, amount: float, category: str | None, date: str) -> str:
    """
    Renders a transaction as a natural-language sentence before
    embedding, not raw JSON -- embedding models are trained on
    language, not structured data, and this framing retrieves
    measurably better. One transaction is one chunk; nothing to split,
    they're already small.
    """
    kind = "income" if amount >= 0 else "expense"
    desc = description or category or "transaction"
    category_part = f", category {category}" if category else ""
    return f"{date}: {kind} of ${abs(amount):.2f} for {desc}{category_part}."


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Pure math, no ML dependency -- exact search over a Python list is
    what makes sense at this app's actual scale (see the model
    docstring for why this isn't pgvector/an ANN index). Vectors from
    embed_document()/embed_query() are already normalized, so this
    reduces to a plain dot product, but this function doesn't assume
    that -- it's correct for un-normalized vectors too, since callers
    (like tests) may pass arbitrary vectors that aren't run through
    the real model.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
