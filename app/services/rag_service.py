"""
Ask Sanchay: retrieval-augmented Q&A over a user's own transactions.

Deliberately a Stage-1 baseline (see the RAG playbook this was built
against): dense retrieval only, one source (transactions), a grounded
prompt with citations and explicit abstention, no sparse/hybrid search,
no reranker, no query-intent routing yet. Those are real, known-next
steps once this baseline has a golden set to measure against -- not
omitted by accident.
"""
import re
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.services.embedding_service import cosine_similarity, embed_query

MIN_RELEVANCE = 0.2  # cosine similarity threshold below which a passage doesn't count as supporting evidence
TOP_K = 8  # passages handed to the model -- more usually makes answers worse, not better (see playbook 5.1)
SYSTEM_PROMPT = """You are Ask Sanchay, a financial assistant answering questions about the \
user's own transaction history.

Grounding rule: answer only using the numbered passages below. Never use outside knowledge \
about finance, and never invent a transaction, amount, or date that isn't in the passages.

Citation rule: after each claim, cite the passage id(s) that support it, in the form [P1]. \
Never cite a passage you were not given.

Abstention rule: if the passages don't contain enough information to answer, say so plainly \
and do not guess.

Style: concise, plain language, dollar amounts formatted normally (e.g. $42.50)."""


@dataclass
class RetrievedPassage:
    transaction_id: str
    label: str  # "P1", "P2", ...
    text: str
    similarity: float


@dataclass
class AskResult:
    answer: str
    sources: list[RetrievedPassage] = field(default_factory=list)
    abstained: bool = False
    grounded: bool = True  # false if the model cited a passage it wasn't given


async def _retrieve(
    db: AsyncSession, *, clerk_user_id: str, question_vector: list[float]
) -> list[RetrievedPassage]:
    # Scoped to this user in the query itself, not filtered after
    # fetching -- the playbook is explicit that this is a correctness
    # boundary, not just a performance one (section 8.2/14.1: never
    # filter entitlements in application code after retrieval).
    query = select(Transaction).where(
        Transaction.clerk_user_id == clerk_user_id,
        Transaction.embedding.is_not(None),
    )
    result = await db.execute(query)
    transactions = result.scalars().all()

    scored = [
        (t, cosine_similarity(question_vector, t.embedding))
        for t in transactions
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = [pair for pair in scored if pair[1] >= MIN_RELEVANCE][:TOP_K]

    passages = []
    for i, (t, score) in enumerate(top, start=1):
        sign = "+" if t.amount >= 0 else "-"
        desc = t.description or t.category or "transaction"
        text = f"{t.date.isoformat()}: {sign}${abs(float(t.amount)):.2f} — {desc}"
        if t.category:
            text += f" ({t.category})"
        passages.append(RetrievedPassage(transaction_id=t.id, label=f"P{i}", text=text, similarity=score))
    return passages


def _build_prompt(question: str, passages: list[RetrievedPassage]) -> str:
    passage_block = "\n".join(f"[{p.label}] {p.text}" for p in passages)
    return f"PASSAGES\n{passage_block}\n\nQUESTION\n{question}"


def _verify_citations(answer: str, passages: list[RetrievedPassage]) -> bool:
    # Deterministic, cheap check (playbook 5.3): every [P#] the model
    # cited must resolve to a passage it was actually given. An
    # unresolvable citation means the answer may not really be
    # grounded in what was supplied.
    valid_labels = {p.label for p in passages}
    cited = set(re.findall(r"\[P(\d+)\]", answer))
    cited_labels = {f"P{n}" for n in cited}
    return cited_labels.issubset(valid_labels)


async def answer_question(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    question: str,
    embed_query_fn: Callable[[str], list[float]] = embed_query,
    generate_fn: Callable[[str, str], str],
) -> AskResult:
    """
    embed_query_fn and generate_fn are both injected rather than
    imported directly, specifically so this function -- the actual
    retrieval and grounding logic -- can be tested without either the
    real embedding model or a live Anthropic API call. generate_fn(system,
    user) -> str is the only shape this function needs from "call an
    LLM"; the router wires up the real Claude call. embed_query_fn is
    named specifically (not embed_fn) because this must be the
    query-side embedder (with bge-small's instruction prefix) -- using
    the document-side embedder here would be exactly the asymmetric-
    encoding mistake embedding_service.py's docstring warns about.
    """
    question_vector = embed_query_fn(question)
    passages = await _retrieve(db, clerk_user_id=clerk_user_id, question_vector=question_vector)

    if not passages:
        return AskResult(
            answer="I don't have any transactions that look relevant to that question.",
            sources=[],
            abstained=True,
        )

    prompt = _build_prompt(question, passages)
    answer = generate_fn(SYSTEM_PROMPT, prompt)
    grounded = _verify_citations(answer, passages)

    return AskResult(answer=answer, sources=passages, abstained=False, grounded=grounded)
