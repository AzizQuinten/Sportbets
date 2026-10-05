from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import unicodedata

import requests


class OpenFootballProvider:
    """Public-domain historical fixture/result bootstrap.

    The map is deliberately explicit: a league is trained only when its Odds API
    key is known to correspond to the OpenFootball competition. Unsupported cups
    and internationals safely remain market-only instead of inheriting the wrong
    domestic league history.
    """

    BASE = 'https://raw.githubusercontent.com/openfootball/football.json/master'
    DATASET_BY_SPORT = {
        'soccer_epl': 'en.1.json',
        'soccer_efl_champ': 'en.2.json',
        'soccer_england_league1': 'en.3.json',
        'soccer_england_league2': 'en.4.json',
        'soccer_germany_bundesliga': 'de.1.json',
        'soccer_germany_bundesliga2': 'de.2.json',
        'soccer_spain_la_liga': 'es.1.json',
        'soccer_spain_segunda_division': 'es.2.json',
        'soccer_italy_serie_a': 'it.1.json',
        'soccer_italy_serie_b': 'it.2.json',
        'soccer_france_ligue_one': 'fr.1.json',
        'soccer_france_ligue_two': 'fr.2.json',
        'soccer_netherlands_eredivisie': 'nl.1.json',
        'soccer_portugal_primeira_liga': 'pt.1.json',
        'soccer_belgium_first_div': 'be.1.json',
        'soccer_scotland_premiership': 'sco.1.json',
        'soccer_turkey_super_league': 'tr.1.json',
        'soccer_austria_bundesliga': 'at.1.json',
        'soccer_greece_super_league': 'gr.1.json',
    }

    TEAM_ALIASES = {
        'AFC Bournemouth': 'Bournemouth',
        'Brighton & Hove Albion FC': 'Brighton and Hove Albion',
        'Manchester United FC': 'Manchester United',
        'Manchester City FC': 'Manchester City',
        'Newcastle United FC': 'Newcastle United',
        'Tottenham Hotspur FC': 'Tottenham Hotspur',
        'Wolverhampton Wanderers FC': 'Wolverhampton Wanderers',
        'West Ham United FC': 'West Ham United',
        'Nottingham Forest FC': 'Nottingham Forest',
        'Crystal Palace FC': 'Crystal Palace',
        'Aston Villa FC': 'Aston Villa',
        'Leicester City FC': 'Leicester City',
        'Ipswich Town FC': 'Ipswich Town',
        'Southampton FC': 'Southampton',
        'Leeds United FC': 'Leeds United',
        'Sunderland AFC': 'Sunderland',
        'Paris Saint-Germain FC': 'Paris Saint Germain',
        'Internazionale Milano': 'Inter Milan',
        'FC Internazionale Milano': 'Inter Milan',
        'Bayern München': 'Bayern Munich',
        '1. FC Köln': 'FC Koln',
        'Athletic Club': 'Athletic Bilbao',
        'Atlético Madrid': 'Atletico Madrid',
    }

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    @classmethod
    def supported_sports(cls) -> list[str]:
        return sorted(cls.DATASET_BY_SPORT)

    @staticmethod
    def _season_labels(seasons: int) -> list[str]:
        now = datetime.now(timezone.utc)
        start_year = now.year if now.month >= 7 else now.year - 1
        labels = []
        for offset in range(max(1, seasons)):
            y = start_year - offset
            labels.append(f'{y}-{str((y + 1) % 100).zfill(2)}')
        return labels

    @classmethod
    def normalize_team(cls, name: str) -> str:
        raw = str(name or '').strip()
        if raw in cls.TEAM_ALIASES:
            return cls.TEAM_ALIASES[raw]
        value = re.sub(r'\s+\([A-Z]{3}\)$', '', raw).strip()
        value = value.replace('&', 'and')
        value = re.sub(r'^(AFC|FC|SC)\s+', '', value, flags=re.I).strip()
        value = re.sub(r'\s+(AFC|FC|CF|SC|AC|AS|Calcio)$', '', value, flags=re.I).strip()
        value = re.sub(r'\s+', ' ', value).strip()
        return cls.TEAM_ALIASES.get(raw, value)

    @staticmethod
    def canonical_key(name: str) -> str:
        value = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode('ascii').lower()
        value = re.sub(r'\b(fc|afc|cf|sc|ac|as|sv|fk|bk|sk|club|football|calcio)\b', ' ', value)
        value = re.sub(r'[^a-z0-9]+', '', value)
        return value

    @staticmethod
    def _full_time_score(score):
        if isinstance(score, list) and len(score) >= 2:
            return score[:2]
        if isinstance(score, dict):
            ft = score.get('ft')
            if isinstance(ft, list) and len(ft) >= 2:
                return ft[:2]
        return None

    @staticmethod
    def _kickoff(match: dict) -> datetime:
        date = match.get('date')
        time = match.get('time') or '12:00'
        if not date:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(f'{date}T{time}:00+00:00')
        except ValueError:
            return datetime.fromisoformat(f'{date}T12:00:00+00:00')

    def fetch_completed(self, sport_key: str, seasons: int = 3) -> list[dict]:
        dataset = self.DATASET_BY_SPORT.get(sport_key)
        if not dataset:
            return []

        now = datetime.now(timezone.utc)
        events: list[dict] = []
        seen: set[str] = set()

        for season in reversed(self._season_labels(seasons)):
            url = f'{self.BASE}/{season}/{dataset}'
            response = requests.get(url, timeout=self.timeout)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json()

            for match in payload.get('matches', []):
                ft = self._full_time_score(match.get('score'))
                if not ft:
                    continue
                kickoff = self._kickoff(match)
                if kickoff > now:
                    continue

                home = self.normalize_team(str(match.get('team1') or '').strip())
                away = self.normalize_team(str(match.get('team2') or '').strip())
                if not home or not away:
                    continue

                raw_id = f'openfootball|{sport_key}|{kickoff.date()}|{home}|{away}'
                event_id = 'of_' + hashlib.sha1(raw_id.encode('utf-8')).hexdigest()[:32]
                if event_id in seen:
                    continue
                seen.add(event_id)

                events.append({
                    'id': event_id,
                    'sport_key': sport_key,
                    'commence_time': kickoff.isoformat().replace('+00:00', 'Z'),
                    'home_team': home,
                    'away_team': away,
                    'completed': True,
                    'scores': [
                        {'name': home, 'score': str(int(ft[0]))},
                        {'name': away, 'score': str(int(ft[1]))},
                    ],
                    'source': 'openfootball',
                    'season': season,
                })

        events.sort(key=lambda x: x['commence_time'])
        return events
