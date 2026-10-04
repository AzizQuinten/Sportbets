from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests


class OddsAPIProvider:
    BASE = 'https://api.the-odds-api.com/v4'
    SCAN_TZ = ZoneInfo('Europe/Amsterdam')

    def __init__(self, api_key: str, region: str = 'eu', timeout: int = 20):
        self.api_key = api_key
        self.region = region
        self.timeout = timeout

    @classmethod
    def _scan_window_utc(cls):
        """UTC boundaries covering today and tomorrow in Dutch local time."""
        now_local = datetime.now(cls.SCAN_TZ)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=2)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    def fetch_odds(self, sport_key: str, markets: list[str]):
        """Fetch normal odds payload, then strictly keep fixtures from today/tomorrow.

        Filtering locally avoids relying on optional API query parameters while still
        guaranteeing that later fixtures never reach signal generation.
        """
        url = f'{self.BASE}/sports/{sport_key}/odds'
        params = {
            'apiKey': self.api_key,
            'regions': self.region,
            'markets': ','.join(markets),
            'oddsFormat': 'decimal',
            'dateFormat': 'iso',
        }
        r = requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        events = r.json()

        start_utc, end_utc = self._scan_window_utc()
        filtered = []
        for event in events:
            raw_time = event.get('commence_time')
            if not raw_time:
                continue
            try:
                commence = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                if commence.tzinfo is None:
                    commence = commence.replace(tzinfo=timezone.utc)
                commence = commence.astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            if start_utc <= commence < end_utc:
                filtered.append(event)

        return filtered, {
            'remaining': r.headers.get('x-requests-remaining'),
            'used': r.headers.get('x-requests-used'),
            'last': r.headers.get('x-requests-last'),
        }

    def fetch_scores(self, sport_key: str, days_from: int = 3):
        url = f'{self.BASE}/sports/{sport_key}/scores'
        r = requests.get(url, params={'apiKey': self.api_key, 'daysFrom': max(1, min(3, int(days_from))), 'dateFormat': 'iso'}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch_score_history(self, sport_key: str, windows: int = 1):
        """Fetch the deepest score window supported by the live API without pretending it is historical data."""
        merged = {}
        # Scores endpoint currently exposes only a short recent window. Keep this helper explicit
        # so a future historical provider can be plugged in without contaminating model code.
        for _ in range(max(1, windows)):
            for event in self.fetch_scores(sport_key, days_from=3):
                if event.get('id'):
                    merged[event['id']] = event
            break
        return list(merged.values())

    @staticmethod
    def flatten(events: list[dict]):
        now = datetime.now(timezone.utc)
        rows = []
        for event in events:
            commence = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
            for book in event.get('bookmakers', []):
                for market in book.get('markets', []):
                    for out in market.get('outcomes', []):
                        rows.append({'event_id': event['id'], 'sport_key': event['sport_key'], 'commence_time': commence, 'home_team': event['home_team'], 'away_team': event['away_team'], 'market': market['key'], 'bookmaker': book['key'], 'outcome': out['name'], 'price': float(out['price']), 'point': out.get('point'), 'observed_at': now})
        return rows
