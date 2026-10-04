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
        now_local = datetime.now(cls.SCAN_TZ)
        end_local = (now_local + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
        return now_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    @staticmethod
    def _iso_utc(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    @staticmethod
    def _parse_time(raw_time):
        if not raw_time:
            return None
        try:
            dt = datetime.fromisoformat(str(raw_time).replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None

    def _get(self, url: str, params: dict, allow_statuses=()):
        r = requests.get(url, params=params, timeout=self.timeout)
        quota = {'remaining': r.headers.get('x-requests-remaining'), 'used': r.headers.get('x-requests-used'), 'last': r.headers.get('x-requests-last')}
        if r.status_code in allow_statuses:
            try:
                payload = r.json()
            except ValueError:
                payload = None
            return r, payload, quota
        try:
            r.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f'Odds API HTTP {r.status_code}: {(r.text or "")[:400]}') from exc
        try:
            return r, r.json(), quota
        except ValueError as exc:
            raise RuntimeError(f'Odds API returned non-JSON HTTP {r.status_code}') from exc

    def fetch_active_sports(self):
        _, payload, _ = self._get(f'{self.BASE}/sports/', {'apiKey': self.api_key, 'all': 'true'})
        if not isinstance(payload, list):
            raise RuntimeError(f'Unexpected sports payload: {type(payload).__name__}')
        return [
            x for x in payload
            if (str(x.get('key', '')).startswith('soccer_') or str(x.get('group', '')).lower().startswith('soccer'))
            and not x.get('has_outrights', False)
        ]

    def fetch_events(self, sport_key: str):
        window_start, window_end = self._scan_window()
        r, payload, _ = self._get(
            f'{self.BASE}/sports/{sport_key}/events',
            {
                'apiKey': self.api_key,
                'dateFormat': 'iso',
                'commenceTimeFrom': self._iso_utc(window_start),
                'commenceTimeTo': self._iso_utc(window_end),
            },
            allow_statuses=(404, 422),
        )
        if r.status_code in (404, 422):
            return [], {
                'http_status': r.status_code,
                'raw_events': 0,
                'eligible_events': 0,
                'past_events': 0,
                'beyond_window': 0,
                'invalid_times': 0,
                'unavailable': True,
                'window_from': self._iso_utc(window_start),
                'window_to': self._iso_utc(window_end),
            }
        if not isinstance(payload, list):
            raise RuntimeError(f'Unexpected events payload for {sport_key}: {type(payload).__name__}')
        eligible = []
        past = future = invalid = 0
        for event in payload:
            commence = self._parse_time(event.get('commence_time'))
            if commence is None:
                invalid += 1
            elif commence < window_start:
                past += 1
            elif commence > window_end:
                future += 1
            else:
                eligible.append(event)
        return eligible, {
            'http_status': r.status_code,
            'raw_events': len(payload),
            'eligible_events': len(eligible),
            'past_events': past,
            'beyond_window': future,
            'invalid_times': invalid,
            'window_from': self._iso_utc(window_start),
            'window_to': self._iso_utc(window_end),
        }

    def fetch_odds(self, sport_key: str, markets: list[str]):
        window_start, window_end = self._scan_window()
        params = {
            'apiKey': self.api_key,
            'regions': self.region,
            'markets': ','.join(markets),
            'oddsFormat': 'decimal',
            'dateFormat': 'iso',
            'commenceTimeFrom': self._iso_utc(window_start),
            'commenceTimeTo': self._iso_utc(window_end),
        }
        r, payload, quota = self._get(
            f'{self.BASE}/sports/{sport_key}/odds/',
            params,
            allow_statuses=(404, 422),
        )

        # The all=true sports catalogue can contain temporarily unavailable or
        # non-priceable competitions. That is a normal catalogue condition, not
        # a fatal scanner failure. Treat it as an empty league and continue.
        if r.status_code in (404, 422):
            meta = {
                'sport_key': sport_key,
                'http_status': r.status_code,
                'raw_events': 0,
                'eligible_events': 0,
                'past_events': 0,
                'beyond_window': 0,
                'invalid_times': 0,
                'raw_bookmakers': 0,
                'raw_markets': 0,
                'raw_outcomes': 0,
                'eligible_bookmakers': 0,
                'eligible_markets': 0,
                'eligible_outcomes': 0,
                'unavailable': True,
                'window_from': self._iso_utc(window_start),
                'window_to': self._iso_utc(window_end),
            }
            self.last_fetch_meta = meta
            return [], {**quota, **meta}

        if not isinstance(payload, list):
            raise RuntimeError(f'Unexpected odds payload for {sport_key}: {type(payload).__name__}')

        filtered = []
        invalid_times = past_events = beyond_window = raw_books = raw_markets = raw_outcomes = 0
        for event in payload:
            books = event.get('bookmakers') or []
            raw_books += len(books)
            for book in books:
                mkts = book.get('markets') or []
                raw_markets += len(mkts)
                for market in mkts:
                    raw_outcomes += len(market.get('outcomes') or [])
            commence = self._parse_time(event.get('commence_time'))
            if commence is None:
                invalid_times += 1
            elif commence < window_start:
                past_events += 1
            elif commence > window_end:
                beyond_window += 1
            else:
                filtered.append(event)

        eligible_books = sum(len(e.get('bookmakers') or []) for e in filtered)
        eligible_markets = sum(len(b.get('markets') or []) for e in filtered for b in (e.get('bookmakers') or []))
        eligible_outcomes = sum(len(m.get('outcomes') or []) for e in filtered for b in (e.get('bookmakers') or []) for m in (b.get('markets') or []))
        meta = {
            'sport_key': sport_key,
            'http_status': r.status_code,
            'raw_events': len(payload),
            'eligible_events': len(filtered),
            'past_events': past_events,
            'beyond_window': beyond_window,
            'invalid_times': invalid_times,
            'raw_bookmakers': raw_books,
            'raw_markets': raw_markets,
            'raw_outcomes': raw_outcomes,
            'eligible_bookmakers': eligible_books,
            'eligible_markets': eligible_markets,
            'eligible_outcomes': eligible_outcomes,
            'window_from': self._iso_utc(window_start),
            'window_to': self._iso_utc(window_end),
        }
        self.last_fetch_meta = meta
        return filtered, {**quota, **meta}

    def fetch_scores(self, sport_key: str, days_from: int = 3):
        r, payload, _ = self._get(
            f'{self.BASE}/sports/{sport_key}/scores/',
            {'apiKey': self.api_key, 'daysFrom': max(1, min(3, int(days_from))), 'dateFormat': 'iso'},
            allow_statuses=(404, 422),
        )
        if r.status_code in (404, 422):
            return []
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
            commence = OddsAPIProvider._parse_time(event.get('commence_time'))
            if commence is None:
                continue
            event_id, sport_key = event.get('id'), event.get('sport_key')
            home, away = event.get('home_team'), event.get('away_team')
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
                        rows.append({
                            'event_id': event_id,
                            'sport_key': sport_key,
                            'commence_time': commence,
                            'home_team': home,
                            'away_team': away,
                            'market': market_key,
                            'bookmaker': book_key,
                            'outcome': outcome,
                            'price': price,
                            'point': out.get('point'),
                            'observed_at': now,
                        })
        return rows
