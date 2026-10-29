"""Create a Supabase Auth user for development, sign in as them, and check the whole login path.

Creates the user with Supabase's admin API (email already confirmed, so no email is sent), signs
in with the password to get a real access token, verifies that token the way the API does
(auth.TokenVerifier against the project's published keys), and creates the matching row in `users`.
If every step prints OK, a frontend sending this token would be accepted.

It needs SUPABASE_SERVICE_ROLE_KEY in .env (Supabase dashboard > Project Settings > API Keys >
service_role). That key bypasses all security, so it stays in .env and is never sent to a frontend.
SUPABASE_ANON_KEY is optional: signing in uses it when present, as the frontend would, and the
service role key otherwise.

Usage (from src/):
    python create_user.py you@example.com [--password PASSWORD] [--name "Your Name"] [--print-token]

Without --password, a random one is generated and printed. If the user already exists, give their
--password to just sign in. The access token expires after an hour; --print-token prints it whole
so it can be pasted into a request: curl -H "Authorization: Bearer <token>" localhost:8000/me
"""
import argparse
import asyncio
import secrets
import sys

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession

from auth import TokenVerifier, default_verifier, get_or_create_user
from config import settings
from database import async_session_maker, engine
from models import User


class SupabaseError(Exception):
    pass


class SupabaseAuth:
    """The two Supabase Auth calls this needs."""

    def __init__(self, base_url: str, service_key: str, sign_in_key: str, client: httpx2.AsyncClient):
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.sign_in_key = sign_in_key
        self.client = client

    @staticmethod
    def explain(response: httpx2.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        return str(body.get("msg") or body.get("message") or body.get("error_description") or body)[:200]

    async def create_user(self, email: str, password: str, full_name: str | None) -> bool:
        """Create a confirmed user. Returns False if the email is already registered."""
        response = await self.client.post(
            f"{self.base_url}/auth/v1/admin/users",
            headers={"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"},
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name} if full_name else {},
            },
        )
        if response.status_code in (200, 201):
            return True
        if response.status_code == 422 and "already" in self.explain(response).lower():
            return False
        raise SupabaseError(f"Creating the user failed ({response.status_code}): {self.explain(response)}")

    async def sign_in(self, email: str, password: str) -> str:
        """The access token for an email and password."""
        response = await self.client.post(
            f"{self.base_url}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": self.sign_in_key},
            json={"email": email, "password": password},
        )
        if response.status_code != 200:
            raise SupabaseError(f"Signing in failed ({response.status_code}): {self.explain(response)}")
        return response.json()["access_token"]


async def run(
    email: str,
    password: str | None,
    full_name: str | None,
    print_token: bool,
    supabase: SupabaseAuth,
    verifier: TokenVerifier,
    db: AsyncSession,
) -> User:
    generated = password is None
    password = password or secrets.token_urlsafe(16)

    created = await supabase.create_user(email, password, full_name)
    if created:
        print(f"OK: created {email} in Supabase Auth.")
    elif generated:
        raise SupabaseError(f"{email} already exists. Run again with --password to sign in as them.")
    else:
        print(f"OK: {email} already exists in Supabase Auth.")

    token = await supabase.sign_in(email, password)
    print("OK: signed in and received an access token.")

    claims = await verifier.verify(token)
    print(f"OK: the token verifies against the project's keys (user {claims.user_id}).")

    user = await get_or_create_user(db, claims)
    print(f"OK: users row {user.id} ({user.email}).")

    if created and generated:
        print(f"\nPassword: {password}")
    print("\nAccess token (valid for about an hour):")
    print(token if print_token else f"{token[:24]}...  (use --print-token to show it whole)")
    return user


async def main(email: str, password: str | None, full_name: str | None, print_token: bool) -> None:
    if not settings.supabase_url or settings.supabase_service_role_key is None:
        raise SystemExit(
            "Set SUPABASE_SERVICE_ROLE_KEY in .env (Supabase dashboard > Project Settings > API Keys > "
            "service_role). SUPABASE_URL is taken from DATABASE_URL."
        )
    service_key = settings.supabase_service_role_key.get_secret_value()
    sign_in_key = (
        settings.supabase_anon_key.get_secret_value() if settings.supabase_anon_key else service_key
    )
    async with httpx2.AsyncClient(timeout=30) as client, async_session_maker() as db:
        supabase = SupabaseAuth(settings.supabase_url, service_key, sign_in_key, client)
        try:
            await run(email, password, full_name, print_token, supabase, default_verifier(), db)
        except SupabaseError as error:
            raise SystemExit(str(error))
    await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Create a development user in Supabase Auth and sign in as them.")
    parser.add_argument("email")
    parser.add_argument("--password", help="the password (generated and printed if omitted)")
    parser.add_argument("--name", help="display name, stored in the user's metadata")
    parser.add_argument("--print-token", action="store_true", help="print the whole access token")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.password, args.name, args.print_token))
