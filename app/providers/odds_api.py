from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import threading
import time
import requests


class OddsAPIProvider:
    BASE = 'https://api.the-odds-api.com/v4'
    SCAN_TZ = ZoneInfo('Europe/Amsterdam')
    # Shared across every provider instance, including discovery worker threads.
    # This prevents concurrent preflight calls from bursting into the provider.
    _request_lock = threading.Lock()
    _last_request_at = 0.0
    MIN_REQUEST_INTERVAL = 1.10

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

    @classmethod
    def _paced_request(cls, url, params, timeout):
        # Keep the lock through the request so all scanner/model workers share one
        # outbound lane. Reliability matters more than shaving seconds off a scan.
        with cls._request_lock:
            wait = cls.MIN_REQUEST_INTERVAL - (time.monotonic() - cls._last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                return requests.get(url, params=params, timeout=timeout)
            finally:
                cls._last_request_at = time.monotonic()

    def _get(self, url: str, params: dict, allow_statuses=()):
        last_exc = None
        # Transport/server errors may retry. 429 gets deliberately slow backoff;
        # the old sub-second retry loop amplified EXCEEDED_FREQ_LIMIT.
        for attempt in range(3):
            try:
                r = self._paced_request(url, params, self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f'Odds API transport error after 3 attempts: {type(exc).__name__}: {exc}') from exc

            quota = {
                'remaining': r.headers.get('x-requests-remaining'),
                'used': r.headers.get('x-requests-used'),
                'last': r.headers.get('x-requests-last'),
            }
            if r.status_code in allow_statuses:
                try: payload = r.json()
                except ValueError: payload = None
                return r, payload, quota

            if r.status_code == 429 and attempt < 2:
                retry_after = r.headers.get('retry-after')
                try: delay = max(5.0, float(retry_after)) if retry_after else 5.0 * (attempt + 1)
                except (TypeError, ValueError): delay = 5.0 * (attempt + 1)
                time.sleep(min(delay, 30.0))
                continue

            if r.status_code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (2 ** attempt))
                continue

            try:
                r.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(f'Odds API HTTP {r.status_code}: {(r.text or "")[:400]}') from exc
            try: return r, r.json(), quota
            except ValueError as exc: raise RuntimeError(f'Odds API returned non-JSON HTTP {r.status_code}') from exc

        raise RuntimeError(f'Odds API request failed: {last_exc or "retry budget exhausted"}')

    def fetch_active_sports(self):
        _, payload, _ = self._get(f'{self.BASE}/sports/', {'apiKey': self.api_key, 'all': 'true'})
        if not isinstance(payload, list): raise RuntimeError(f'Unexpected sports payload: {type(payload).__name__}')
        return [x for x in payload if (str(x.get('key','')).startswith('soccer_') or str(x.get('group','')).lower().startswith('soccer')) and not x.get('has_outrights',False)]

    def fetch_events(self, sport_key: str):
        start, end = self._scan_window()
        r, payload, _ = self._get(f'{self.BASE}/sports/{sport_key}/events', {'apiKey':self.api_key,'dateFormat':'iso','commenceTimeFrom':self._iso_utc(start),'commenceTimeTo':self._iso_utc(end)}, allow_statuses=(404,422))
        base={'http_status':r.status_code,'window_from':self._iso_utc(start),'window_to':self._iso_utc(end)}
        if r.status_code in (404,422): return [], {**base,'raw_events':0,'eligible_events':0,'past_events':0,'beyond_window':0,'invalid_times':0,'unavailable':True}
        if not isinstance(payload,list): raise RuntimeError(f'Unexpected events payload for {sport_key}: {type(payload).__name__}')
        eligible=[]; past=future=invalid=0
        for event in payload:
            commence=self._parse_time(event.get('commence_time'))
            if commence is None: invalid+=1
            elif commence < start: past+=1
            elif commence > end: future+=1
            else: eligible.append(event)
        return eligible,{**base,'raw_events':len(payload),'eligible_events':len(eligible),'past_events':past,'beyond_window':future,'invalid_times':invalid}

    def fetch_odds(self, sport_key: str, markets: list[str]):
        start,end=self._scan_window()
        params={'apiKey':self.api_key,'regions':self.region,'markets':','.join(markets),'oddsFormat':'decimal','dateFormat':'iso','commenceTimeFrom':self._iso_utc(start),'commenceTimeTo':self._iso_utc(end)}
        r,payload,quota=self._get(f'{self.BASE}/sports/{sport_key}/odds/',params,allow_statuses=(404,422))
        base={'sport_key':sport_key,'http_status':r.status_code,'window_from':self._iso_utc(start),'window_to':self._iso_utc(end)}
        if r.status_code in (404,422):
            meta={**base,'raw_events':0,'eligible_events':0,'past_events':0,'beyond_window':0,'invalid_times':0,'raw_bookmakers':0,'raw_markets':0,'raw_outcomes':0,'eligible_bookmakers':0,'eligible_markets':0,'eligible_outcomes':0,'unavailable':True}; self.last_fetch_meta=meta; return [],{**quota,**meta}
        if not isinstance(payload,list): raise RuntimeError(f'Unexpected odds payload for {sport_key}: {type(payload).__name__}')
        filtered=[]; invalid=past=future=raw_books=raw_markets=raw_outcomes=0
        for event in payload:
            books=event.get('bookmakers') or []; raw_books+=len(books)
            for book in books:
                mkts=book.get('markets') or []; raw_markets+=len(mkts)
                for market in mkts: raw_outcomes+=len(market.get('outcomes') or [])
            commence=self._parse_time(event.get('commence_time'))
            if commence is None: invalid+=1
            elif commence < start: past+=1
            elif commence > end: future+=1
            else: filtered.append(event)
        eb=sum(len(e.get('bookmakers') or []) for e in filtered)
        em=sum(len(b.get('markets') or []) for e in filtered for b in (e.get('bookmakers') or []))
        eo=sum(len(m.get('outcomes') or []) for e in filtered for b in (e.get('bookmakers') or []) for m in (b.get('markets') or []))
        meta={**base,'raw_events':len(payload),'eligible_events':len(filtered),'past_events':past,'beyond_window':future,'invalid_times':invalid,'raw_bookmakers':raw_books,'raw_markets':raw_markets,'raw_outcomes':raw_outcomes,'eligible_bookmakers':eb,'eligible_markets':em,'eligible_outcomes':eo}; self.last_fetch_meta=meta
        return filtered,{**quota,**meta}

    def fetch_scores(self, sport_key: str, days_from: int = 3):
        r,payload,_=self._get(f'{self.BASE}/sports/{sport_key}/scores/',{'apiKey':self.api_key,'daysFrom':max(1,min(3,int(days_from))),'dateFormat':'iso'},allow_statuses=(404,422))
        if r.status_code in (404,422): return []
        return payload if isinstance(payload,list) else []

    def fetch_score_history(self, sport_key: str, windows: int = 1):
        merged={}
        for event in self.fetch_scores(sport_key,days_from=3):
            if event.get('id'): merged[event['id']]=event
        return list(merged.values())

    @staticmethod
    def flatten(events: list[dict]):
        now=datetime.now(timezone.utc); rows=[]
        for event in events:
            commence=OddsAPIProvider._parse_time(event.get('commence_time'))
            if commence is None: continue
            event_id,sport_key=event.get('id'),event.get('sport_key'); home,away=event.get('home_team'),event.get('away_team')
            if not all((event_id,sport_key,home,away)): continue
            for book in event.get('bookmakers') or []:
                book_key=book.get('key')
                if not book_key: continue
                for market in book.get('markets') or []:
                    market_key=market.get('key')
                    if not market_key: continue
                    for out in market.get('outcomes') or []:
                        try: price=float(out['price'])
                        except (KeyError,TypeError,ValueError): continue
                        outcome=out.get('name')
                        if not outcome or price<=1.0: continue
                        rows.append({'event_id':event_id,'sport_key':sport_key,'commence_time':commence,'home_team':home,'away_team':away,'market':market_key,'bookmaker':book_key,'outcome':outcome,'price':price,'point':out.get('point'),'observed_at':now})
        return rows
