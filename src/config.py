import re
from pathlib import Path
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    # The Supabase project URL, such as https://abcdefgh.supabase.co. If it isn't set, it is taken
    # from a direct database URL (db.<project>.supabase.co). Set it when DATABASE_URL points at the
    # connection pooler, whose host doesn't contain the project.
    supabase_url: str | None = None
    # Only create_user.py needs these, to create test users and sign in as them. The service role key
    # bypasses all security, so keep it in .env only: never in the frontend, never committed.
    supabase_service_role_key: SecretStr | None = None
    supabase_anon_key: SecretStr | None = None

    # Limits that keep each user's analysis cost bounded (see "Cost controls" in CLAUDE.md).
    max_upload_bytes: int = 5 * 1024 * 1024
    max_upload_rows: int = 5000
    max_entities_per_user: int = 20
    max_runs_per_user_per_day: int = 20

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / "../.env",
        env_file_encoding="utf-8",
    )

    @model_validator(mode="after")
    def fill_supabase_url(self):
        if self.supabase_url is None:
            match = re.search(r"@db\.([a-z0-9]+)\.supabase\.co", self.database_url)
            if match:
                self.supabase_url = f"https://{match.group(1)}.supabase.co"
        return self

settings = Settings()
