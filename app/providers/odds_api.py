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
        """Return the UTC window covering today + tomorrow in Dutch local time.

        This keeps the betting engine focused on actionable fixtures and prevents
        next-week matches from entering signal generation, shadow research or the
        paper ledger. DST is handled by Europe/Amsterdam automatically.
        """
        now_local = datetime.now(cls.SCAN_TZ)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=2)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    def fetch_odds(self, sport_key: str, markets: list[str]):
        url = f'{self.BASE}/sports/{sport_key}/odds'
        start_utc, end_utc = self._scan_window_utc()
        params = {
            'apiKey': self.api_key,
            'regions': self.region,
            'markets': ','.join(markets),
            'oddsFormat': 'decimal',
            'dateFormat': 'iso',
            'commenceTimeFrom': start_utc.isoformat().replace('+00:00', 'Z'),
            # The API upper bound is inclusive, so subtract a microsecond to keep
            # fixtures at 00:00 the day after tomorrow out of the scan.
            'commenceTimeTo': (end_utc - timedelta(microseconds=1)).isoformat().replace('+00:00', 'Z'),
        }
        r = requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json(), {
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
