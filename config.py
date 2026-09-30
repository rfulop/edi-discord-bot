import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must contain a valid Discord ID") from exc


@dataclass(frozen=True)
class Settings:
    discord_token: str | None
    guild_id: int | None
    voice_channel_id: int | None
    application_id: int | None

    @classmethod
    def from_environment(cls):
        return cls(
            discord_token=os.getenv("DISCORD_TOKEN"),
            guild_id=optional_int("GUILD_ID"),
            voice_channel_id=optional_int("VOICE_CHANNEL_ID"),
            application_id=optional_int("APP_ID"),
        )

    def validate_runtime(self):
        missing = []
        if not self.discord_token:
            missing.append("DISCORD_TOKEN")
        if not self.application_id:
            missing.append("APP_ID")
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


settings = Settings.from_environment()
