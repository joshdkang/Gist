"""Authentication with Supabase Auth.

The frontend signs the user in with Supabase and sends the access token as a bearer token. This
verifies it and finds (or creates, on first sight) the matching row in `users`, whose ID is the
Supabase user's ID. FastAPI never handles passwords.

Verification uses the project's public signing keys, published at
<project>/auth/v1/.well-known/jwks.json (JWT signing keys, asymmetric). Only asymmetric algorithms
are accepted: a token signed with a shared secret (HS256) or with no signature is rejected, so
there is no secret to keep and no way to forge a token with the public key.
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import User

logger = logging.getLogger(__name__)

ALGORITHMS = ["ES256", "RS256", "EdDSA"]  # asymmetric only
AUDIENCE = "authenticated"  # the audience of a signed-in user's access token
LEEWAY_SECONDS = 10  # clock difference tolerated when checking exp
JWKS_CACHE_SECONDS = 3600


class TokenError(Exception):
    """The token is missing something, expired, or not signed by this project."""


class SigningKeysUnavailable(Exception):
    """The project's public keys couldn't be fetched, so no token can be checked right now."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    email: str
    display_name: str | None


class TokenVerifier:
    def __init__(self, issuer: str, jwks_url: str, jwks_client: PyJWKClient | None = None):
        self.issuer = issuer
        self.jwks_client = jwks_client or PyJWKClient(
            jwks_url, cache_keys=True, lifespan=JWKS_CACHE_SECONDS, timeout=10
        )

    async def verify(self, token: str) -> TokenClaims:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm not in ALGORITHMS:
                raise TokenError(f"unsupported signing algorithm {algorithm!r}")
            # PyJWKClient fetches the keys with blocking I/O, so keep it off the event loop.
            signing_key = await asyncio.to_thread(self.jwks_client.get_signing_key_from_jwt, token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                audience=AUDIENCE,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.PyJWKClientConnectionError as error:
            raise SigningKeysUnavailable(str(error)) from error
        except jwt.PyJWTError as error:
            raise TokenError(str(error)) from error

        try:
            user_id = uuid.UUID(payload["sub"])
        except ValueError as error:
            raise TokenError("the subject is not a user ID") from error
        email = payload.get("email")
        if payload.get("is_anonymous") or not email:
            raise TokenError("the token has no email address")
        metadata = payload.get("user_metadata") or {}
        return TokenClaims(user_id, email, metadata.get("full_name") or metadata.get("name"))


@lru_cache
def default_verifier() -> TokenVerifier:
    if not settings.supabase_url:
        raise RuntimeError("Set SUPABASE_URL in .env: it couldn't be taken from DATABASE_URL.")
    base_url = settings.supabase_url.rstrip("/")
    return TokenVerifier(
        issuer=f"{base_url}/auth/v1", jwks_url=f"{base_url}/auth/v1/.well-known/jwks.json"
    )


def get_verifier() -> TokenVerifier:
    """A dependency so tests can substitute their own keys."""
    return default_verifier()


async def get_or_create_user(db: AsyncSession, claims: TokenClaims) -> User:
    """The user row for a verified token, created the first time that user is seen."""
    user = await db.get(User, claims.user_id)
    try:
        if user is None:
            await db.execute(
                postgres_insert(User)
                .values(id=claims.user_id, email=claims.email, display_name=claims.display_name)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await db.commit()
            user = await db.get(User, claims.user_id)
        elif user.email != claims.email:  # the user changed their email in Supabase
            user.email = claims.email
            await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Another account already uses this email address."
        ) from error
    return user


bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    verifier: TokenVerifier = Depends(get_verifier),
    db: AsyncSession = Depends(get_db),
) -> User:
    """The signed-in user. Every endpoint that touches user data depends on this."""
    if credentials is None:
        raise unauthorized("Not signed in.")
    try:
        claims = await verifier.verify(credentials.credentials)
    except SigningKeysUnavailable as error:
        logger.error("Couldn't fetch the Supabase signing keys: %s", error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.")
    except TokenError as error:
        logger.info("Rejected a token: %s", error)  # the caller only learns that it was rejected
        raise unauthorized("Invalid or expired token.")
    return await get_or_create_user(db, claims)
