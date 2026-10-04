from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_name: str = 'SportBet Edge Lab'
    env: str = 'development'
    database_url: str = 'sqlite:///./sportbet.db'
    odds_api_key: str = ''
    odds_region: str = 'eu'
    sport_keys: str = 'soccer_epl'
    markets: str = 'h2h'
    bankroll: float = 10000.0
    paper_only: bool = True
    min_bookmakers: int = 4
    min_edge: float = 0.035
    min_ev: float = 0.025
    max_vig: float = 0.10
    kelly_fraction: float = 0.20
    max_stake_pct: float = 0.0075
    max_event_exposure_pct: float = 0.0125
    max_daily_exposure_pct: float = 0.035
    max_odds_age_seconds: int = 600
    poll_minutes: int = 5

    # V1.1 engine controls.
    auto_scan_enabled: bool = True
    auto_settle_enabled: bool = True
    api_quota_floor: int = 40
    snapshot_retention_days: int = 30
    signal_retention_days: int = 30

    # V1.2 model intelligence. The independent model starts with low influence
    # and earns more weight only as completed matches accumulate.
    max_model_weight: float = 0.40
    elo_home_advantage: float = 55.0
    elo_k_factor: float = 24.0
    model_full_strength_games: int = 10
    model_refresh_hours: int = 12

    # V1.4 historical bootstrap. Public-domain historical results are used only
    # for competitions that have an explicit provider mapping.
    historical_bootstrap_enabled: bool = True
    historical_bootstrap_seasons: int = 2

    # Shadow tier: track plausible near-misses without risking bankroll.
    shadow_min_bookmakers: int = 3
    shadow_min_edge: float = 0.005
    shadow_min_ev: float = 0.005

    @property
    def sports(self):
        return [x.strip() for x in self.sport_keys.split(',') if x.strip()]

    @property
    def market_list(self):
        return [x.strip() for x in self.markets.split(',') if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
