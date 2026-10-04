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
        self.last_fetch_meta = {}

    @classmethod
    def _scan_window(cls):
        """Return UTC instants covering today + tomorrow in Amsterdam time."""
        now_local = datetime.now(cls.SCAN_TZ)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=2)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    def fetch_odds(self, sport_key: str, markets: list[str]):
        """Fetch only fixtures from today and tomorrow (Europe/Amsterdam).

        The date bounds are sent to The Odds API as UTC timestamps so next-week
        fixtures are not downloaded and then discarded locally. A defensive local
        check remains in place for provider boundary quirks/DST.
        """
        window_start, window_end = self._scan_window()
        url = f'{self.BASE}/sports/{sport_key}/odds'
        params = {
            'apiKey': self.api_key,
            'regions': self.region,
            'markets': ','.join(markets),
            'oddsFormat': 'decimal',
            'dateFormat': 'iso',
            'commenceTimeFrom': window_start.isoformat().replace('+00:00', 'Z'),
            'commenceTimeTo': window_end.isoformat().replace('+00:00', 'Z'),
        }
        r = requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        events = payload if isinstance(payload, list) else []

        filtered = []
        invalid_times = 0
        for event in events:
            raw_time = event.get('commence_time')
            if not raw_time:
                invalid_times += 1
                continue
            try:
                commence = datetime.fromisoformat(str(raw_time).replace('Z', '+00:00'))
                if commence.tzinfo is None:
                    commence = commence.replace(tzinfo=timezone.utc)
                commence = commence.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                invalid_times += 1
                continue
            if window_start <= commence < window_end:
                filtered.append(event)

        self.last_fetch_meta = {
            'sport_key': sport_key,
            'raw_events': len(events),
            'eligible_events': len(filtered),
            'filtered_events': max(0, len(events) - len(filtered)),
            'invalid_times': invalid_times,
            'window_from': window_start.isoformat(),
            'window_to': window_end.isoformat(),
        }
        return filtered, {
            'remaining': r.headers.get('x-requests-remaining'),
            'used': r.headers.get('x-requests-used'),
            'last': r.headers.get('x-requests-last'),
            **self.last_fetch_meta,
        }

    def fetch_scores(self, sport_key: str, days_from: int = 3):
        url = f'{self.BASE}/sports/{sport_key}/scores'
        r = requests.get(url, params={'apiKey': self.api_key, 'daysFrom': max(1, min(3, int(days_from))), 'dateFormat': 'iso'}, timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        return payload if isinstance(payload, list) else []

    def fetch_score_history(self, sport_key: str, windows: int = 1):
        merged = {}
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
            raw_time = event.get('commence_time')
            if not raw_time:
                continue
            try:
                commence = datetime.fromisoformat(str(raw_time).replace('Z', '+00:00'))
                if commence.tzinfo is None:
                    commence = commence.replace(tzinfo=timezone.utc)
                commence = commence.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
            event_id = event.get('id')
            sport_key = event.get('sport_key')
            home = event.get('home_team')
            away = event.get('away_team')
            if not all((event_id, sport_key, home, away)):
                continue
            for book in event.get('bookmakers') or []:
                book_key = book.get('key')
                if not book_key:
                    continue
                for market in book.get('markets') or []:
                    market_key = market.get('key')
                    if not market_key:
                        continue
                    for out in market.get('outcomes') or []:
                        try:
                            price = float(out['price'])
                        except (KeyError, TypeError, ValueError):
                            continue
                        outcome = out.get('name')
                        if not outcome or price <= 1.0:
                            continue
                        rows.append({'event_id': event_id, 'sport_key': sport_key, 'commence_time': commence, 'home_team': home, 'away_team': away, 'market': market_key, 'bookmaker': book_key, 'outcome': outcome, 'price': price, 'point': out.get('point'), 'observed_at': now})
        return rows
