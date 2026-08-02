"""
Clerk token verification -- for the NEW (accounts/transactions/budgets)
routes only. Every existing route in this app (auth, sync, shared
expenses, health, legal) keeps using app/core/security.py's own
HS256 JWT, issued and verified with this service's own
jwt_secret_key, completely unrelated to any of this.

Two parallel auth mechanisms on one backend, deliberately -- this is
additive to the existing system, not a replacement of it.

"Networkless" verification (Clerk's own term for this): the token is
verified locally against a public key already in this service's own
config, not by calling Clerk's API on every request. Faster (no
network round-trip per request) and keeps working even if Clerk's API
has a bad moment -- the tradeoff is this service can't check
Clerk-side revocation state (e.g. a session Clerk revoked seconds ago
still verifies as valid here until it naturally expires). Acceptable
here: access tokens are short-lived by Clerk's own default, and the
alternative (a network call to Clerk on every single request to this
API) is a real latency and availability cost for a guarantee this app
doesn't currently need.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)

_unauthorized = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_clerk_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None:
        raise _unauthorized

    if not settings.clerk_jwt_key:
        # Fails loudly rather than silently accepting unverifiable
        # tokens -- same philosophy as jwt_secret_key having no
        # fallback default elsewhere in this config. A misconfigured
        # deployment should refuse every request to these routes, not
        # quietly skip verification.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Clerk verification is not configured on this server",
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.clerk_jwt_key,
            algorithms=["RS256"],
        )
    except JWTError:
        raise _unauthorized

    # azp (authorized party): which frontend origin this token was
    # actually issued for. Clerk's own docs are explicit that skipping
    # this check opens a CSRF-shaped hole -- a token minted for a
    # different, unrelated Clerk-using application could otherwise be
    # replayed against this API. Only skipped if genuinely unconfigured
    # (local dev before CLERK_AUTHORIZED_PARTIES is set) -- enforced
    # unconditionally once it is.
    authorized_parties = settings.clerk_authorized_parties_list
    if authorized_parties:
        azp = payload.get("azp")
        if azp not in authorized_parties:
            raise _unauthorized

    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized

    return user_id
