from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BUDGET_", extra="ignore")

    app_name: str = "Family Budget Offline"
    database_url: str = "sqlite:///./family_budget.db"
    data_dir: Path = Path("./data")
    dedup_date_window_days: int = 2
    transfer_window_days: int = 3

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


settings = Settings()
