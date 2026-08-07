from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_auth import get_clerk_user_id
from app.core.config import settings
from app.core.database import get_sanchay_app_db
from app.core.limiter import limiter
from app.schemas.ai import AskRequest, AskResponseOut, AskSourceOut
from app.services import rag_service
from app.services.embedding_service import get_query_embed_fn

router = APIRouter(prefix="/ai", tags=["ai"])


def _generate_with_claude(system: str, user: str) -> str:
    # Imported lazily so importing this router module (and therefore
    # main.py) never requires the anthropic package to be installed
    # at all -- keeps this feature's dependency contained to the one
    # place it's actually used, same reasoning as the lazy
    # sentence-transformers import in embedding_service.py.
    if not settings.anthropic_api_key:
        # A clear, specific error instead of a confusing failure deep
        # inside the Anthropic SDK when the key is simply unset. Lives
        # here, not as a router-level precondition, so it only fires
        # for the real implementation -- a test-injected generate_fn
        # stub never needs a key at all.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Ask Sanchay isn't configured yet")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def get_generate_fn():
    # Same overridable-dependency pattern as get_query_embed_fn -- tests
    # override this with a stub so exercising the /ai/ask endpoint
    # itself never requires a live Anthropic API key or network call.
    return _generate_with_claude


@router.post("/ask", response_model=AskResponseOut)
@limiter.limit("20/minute")
async def ask(
    request: Request,
    payload: AskRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
    embed_fn=Depends(get_query_embed_fn),
    generate_fn=Depends(get_generate_fn),
) -> AskResponseOut:
    result = await rag_service.answer_question(
        db, clerk_user_id=clerk_user_id, question=payload.question, embed_query_fn=embed_fn, generate_fn=generate_fn
    )
    return AskResponseOut(
        answer=result.answer,
        sources=[AskSourceOut(label=s.label, text=s.text, similarity=s.similarity) for s in result.sources],
        abstained=result.abstained,
        grounded=result.grounded,
    )
