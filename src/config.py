import re
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    # The Supabase project URL, such as https://abcdefgh.supabase.co. If it isn't set, it is taken
    # from a direct database URL (db.<project>.supabase.co). Set it when DATABASE_URL points at the
    # connection pooler, whose host doesn't contain the project.
    supabase_url: str | None = None

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
