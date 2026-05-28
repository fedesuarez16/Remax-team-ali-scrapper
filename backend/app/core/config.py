from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str = ""        # for local JWT verification
    DATABASE_URL: str                     # asyncpg dsn (app pool)
    CHECKPOINTER_DSN: str = ""            # psycopg dsn; falls back to DATABASE_URL
    REDIS_URL: str = "redis://redis:6379"
    ANTHROPIC_API_KEY: str = ""
    APIFY_API_TOKEN: str = ""
    YCLOUD_API_KEY: str = ""
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
