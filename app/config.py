from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:////data/trading.db"
    data_dir: str = "/data"
    traderspost_webhook: str = ""
    tradingview_secret: str = "change-me"
    enable_live_ordering: bool = False

    model_threshold_long: float = 0.58
    model_threshold_short: float = 0.42
    risk_atr_multiplier: float = 1.8
    take_profit_r_multiplier: float = 2.2
    max_bars_per_symbol: int = 3000

    position_size_mes: int = 1
    position_size_mnq: int = 1
    position_size_mgc: int = 1

    default_time_in_force: str = "day"
    request_timeout_seconds: float = 15.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "models").mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
