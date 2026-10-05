from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_name: str = 'SportBet Edge Lab'
    env: str = 'development'
    database_url: str = 'sqlite:///./sportbet.db'
    odds_api_key: str = ''
    odds_region: str = 'eu'
    sport_keys: str = (
        'soccer_epl,'
        'soccer_uefa_champs_league,'
        'soccer_uefa_europa_league,'
        'soccer_uefa_europa_conference_league,'
        'soccer_spain_la_liga,'
        'soccer_germany_bundesliga,'
        'soccer_italy_serie_a,'
        'soccer_france_ligue_one,'
        'soccer_netherlands_eredivisie'
    )
    markets: str = 'h2h'
    bankroll: float = 10000.0
    paper_only: bool = True

    # CORE: deliberately selective and never relaxed just to force action.
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

    # EXPLORATION: model-led near misses with tiny paper stakes.
    exploration_enabled: bool = True
    exploration_max_bets_per_scan: int = 3
    exploration_min_bookmakers: int = 3
    exploration_min_edge: float = 0.0075
    exploration_min_ev: float = 0.010
    exploration_min_market_quality: float = 0.60
    exploration_min_confidence: float = 0.55
    exploration_max_stake_pct: float = 0.0015
    exploration_max_daily_exposure_pct: float = 0.006

    # SCOUT: independent market-price research. Uses the no-vig consensus as the
    # probability anchor, not an immature model. It exists so active slates can
    # generate small, auditable paper observations without weakening CORE.
    scout_enabled: bool = True
    scout_max_bets_per_scan: int = 2
    scout_min_bookmakers: int = 2
    scout_min_market_edge: float = 0.0005
    scout_min_market_ev: float = 0.0010
    scout_min_market_quality: float = 0.45
    scout_min_odds: float = 1.25
    scout_max_odds: float = 7.00
    scout_max_stake_pct: float = 0.0005
    scout_max_daily_exposure_pct: float = 0.002

    auto_scan_enabled: bool = True
    auto_settle_enabled: bool = True
    api_quota_floor: int = 40
    snapshot_retention_days: int = 30
    signal_retention_days: int = 30

    max_model_weight: float = 0.40
    elo_home_advantage: float = 55.0
    elo_k_factor: float = 24.0
    model_full_strength_games: int = 10
    model_refresh_hours: int = 12

    min_model_reliability_for_paper: float = 0.75
    max_model_market_gap: float = 0.20
    max_blended_model_shift: float = 0.06
    max_paper_ev: float = 0.30
    one_pick_per_event_market: bool = True

    historical_bootstrap_enabled: bool = True
    historical_bootstrap_seasons: int = 2

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
