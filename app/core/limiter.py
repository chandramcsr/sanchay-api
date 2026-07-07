"""
Rate limiting, keyed by client IP. Login and signup are the endpoints
worth protecting first — unrestricted, they're a brute-force and
credential-stuffing target the moment the API is reachable from the
internet, which it now is.

Limits are intentionally generous for a real user (a few typos while
signing in) and restrictive for a script (dozens of attempts).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
