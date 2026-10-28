"""Check that the database can't be reached through Supabase's Data API.

Every table in the public schema must have row level security on, and the backend's database role
must bypass it (otherwise the backend itself would be locked out). On Supabase it also checks that
the `anon` and `authenticated` roles hold no privileges on those tables. Exits with status 1 if
anything is wrong, so it can run in CI and after migrations.

Usage (from src/):
    python check_rls.py
"""
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from database import engine


async def find_problems(connection: AsyncConnection) -> list[str]:
    problems = []

    role = (
        await connection.execute(
            text("SELECT current_user, rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).one()
    if not role[1]:
        problems.append(f"The backend role '{role[0]}' does not bypass row level security.")

    unprotected = (
        await connection.execute(
            text("""
                SELECT relname FROM pg_class
                WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' AND NOT relrowsecurity
                ORDER BY relname
            """)
        )
    ).scalars().all()
    problems += [f"Row level security is off on public.{table}." for table in unprotected]

    exposed = (
        await connection.execute(
            text("""
                SELECT grantee, table_name, count(*) FROM information_schema.role_table_grants
                WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
                GROUP BY grantee, table_name ORDER BY grantee, table_name
            """)
        )
    ).all()
    problems += [f"Role '{grantee}' still has privileges on public.{table}." for grantee, table, _ in exposed]
    return problems


async def main() -> int:
    async with engine.connect() as connection:
        problems = await find_problems(connection)
    await engine.dispose()
    if problems:
        print("\n".join(problems))
        return 1
    print("OK: row level security is on for every public table, and the Data API roles have no access.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
