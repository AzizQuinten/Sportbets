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
        'soccer_efl_champ,'
        'soccer_uefa_champs_league,'
        'soccer_uefa_europa_league,'
        'soccer_uefa_europa_conference_league,'
        'soccer_spain_la_liga,'
        'soccer_spain_segunda_division,'
        'soccer_germany_bundesliga,'
        'soccer_germany_bundesliga2,'
        'soccer_italy_serie_a,'
        'soccer_italy_serie_b,'
        'soccer_france_ligue_one,'
        'soccer_france_ligue_two,'
        'soccer_netherlands_eredivisie,'
        'soccer_portugal_primeira_liga,'
        'soccer_belgium_first_div,'
        'soccer_scotland_premiership,'
        'soccer_turkey_super_league'
    )
    markets: str = 'h2h'
    bankroll: float = 10000.0
    paper_only: bool = True

    # CORE: evidence-led, adaptive but never forced.
    min_bookmakers: int = 4
    min_edge: float = 0.035
    min_ev: float = 0.025
    min_market_quality: float = 0.50
    min_confidence: float = 0.52
    min_core_odds: float = 1.20
    max_core_odds: float = 8.00
    max_vig: float = 0.10
    kelly_fraction: float = 0.20
    max_stake_pct: float = 0.0075
    max_event_exposure_pct: float = 0.0125
    max_daily_exposure_pct: float = 0.035
    max_league_daily_exposure_pct: float = 0.015
    max_odds_age_seconds: int = 600
    poll_minutes: int = 5

    # Dynamic gates: clean liquid markets with mature model evidence can earn a
    # modest relaxation; weak/long-shot markets become stricter instead.
    elite_market_quality: float = 0.78
    elite_model_reliability: float = 0.80
    elite_gate_multiplier: float = 0.72
    longshot_odds_1: float = 3.50
    longshot_odds_2: float = 5.50
    longshot_gate_multiplier_1: float = 1.12
    longshot_gate_multiplier_2: float = 1.28

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

    # SCOUT: market-consensus dislocation research, independent from model
    # maturity. Reference probability excludes the best-price bookmaker when
    # possible to avoid self-referential edge.
    scout_enabled: bool = True
    scout_max_bets_per_scan: int = 3
    scout_min_bookmakers: int = 3
    scout_min_market_edge: float = 0.0030
    scout_min_market_ev: float = 0.0050
    scout_min_market_quality: float = 0.50
    scout_min_odds: float = 1.25
    scout_max_odds: float = 6.00
    scout_max_stake_pct: float = 0.0005
    scout_max_daily_exposure_pct: float = 0.0025

    auto_scan_enabled: bool = True
    auto_settle_enabled: bool = True
    api_quota_floor: int = 40
    snapshot_retention_days: int = 45
    signal_retention_days: int = 45

    # Independent model controls.
    max_model_weight: float = 0.45
    elo_home_advantage: float = 55.0
    elo_k_factor: float = 22.0
    model_full_strength_games: int = 12
    model_refresh_hours: int = 8
    model_recent_matches: int = 20
    model_team_prior_games: float = 8.0
    model_recency_half_life_days: float = 120.0
    model_freshness_half_life_days: float = 90.0
    dixon_coles_rho: float = -0.06

    min_model_reliability_for_paper: float = 0.70
    max_model_market_gap: float = 0.18
    max_blended_model_shift: float = 0.055
    max_paper_ev: float = 0.25
    one_pick_per_event_market: bool = True

    historical_bootstrap_enabled: bool = True
    historical_bootstrap_seasons: int = 3
    bootstrap_all_supported_leagues: bool = True

    shadow_min_bookmakers: int = 3
    shadow_min_edge: float = 0.004
    shadow_min_ev: float = 0.004

    @property
    def sports(self):
        return [x.strip() for x in self.sport_keys.split(',') if x.strip()]

    @property
    def market_list(self):
        return [x.strip() for x in self.markets.split(',') if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
