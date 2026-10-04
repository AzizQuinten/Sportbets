from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re

import requests


class OpenFootballProvider:
    """Public-domain historical fixture/result bootstrap.

    Source: openfootball/football.json (public domain / CC0-style project).
    We intentionally keep the supported competition map explicit so we never
    silently train a league model on the wrong competition.
    """

    BASE = 'https://raw.githubusercontent.com/openfootball/football.json/master'
    DATASET_BY_SPORT = {
        'soccer_epl': 'en.1.json',
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
    }

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

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
        if name in cls.TEAM_ALIASES:
            return cls.TEAM_ALIASES[name]
        value = re.sub(r'\s+\([A-Z]{3}\)$', '', name).strip()
        value = value.replace('&', 'and')
        value = re.sub(r'\s+FC$', '', value).strip()
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

    def fetch_completed(self, sport_key: str, seasons: int = 2) -> list[dict]:
        dataset = self.DATASET_BY_SPORT.get(sport_key)
        if not dataset:
            return []

        now = datetime.now(timezone.utc)
        events: list[dict] = []
        seen: set[str] = set()

        # Oldest first so Elo state is built chronologically when ingested.
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
