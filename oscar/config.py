from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class PollSettings(BaseModel):
    base_interval: int = 20
    jitter: int = 10

    @field_validator("base_interval")
    @classmethod
    def enforce_floor(cls, v: int) -> int:
        if v < 10:
            raise ValueError("base_interval must be >= 10 seconds — hard floor")
        return v

class CRNConfig(BaseModel):
    crn: str
    label: str = ""
    retry_on_restriction: bool = False

class Config(BaseModel):
    term: str
    crns: list[CRNConfig]
    poll: PollSettings = PollSettings()
    cookies_path: Path = Path("session.json")
    db_path: Path = Path("oscar.db")
    log_dir: Path = Path("logs")
    dry_run: bool = False

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    pushover_token: str = ""
    pushover_user_key: str = ""
    vps_host: str = ""
    vps_user: str = "ubuntu"
    vps_cookies_path: str = "~/oscar/session.json"
    browser_profile_dir: Path = Path.home() / ".oscar" / "browser_profile"
    config_path: Path = Path("config.yaml")

    def load_config(self) -> Config:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}. Copy config.example.yaml to {self.config_path} and edit it.")
        with open(self.config_path) as f:
            data = yaml.safe_load(f)
        return Config(**data)
