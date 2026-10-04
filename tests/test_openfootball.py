from app.providers.openfootball import OpenFootballProvider


def test_team_normalization_matches_live_epl_names():
    assert OpenFootballProvider.normalize_team('Arsenal FC') == 'Arsenal'
    assert OpenFootballProvider.normalize_team('AFC Bournemouth') == 'Bournemouth'
    assert OpenFootballProvider.normalize_team('Brighton & Hove Albion FC') == 'Brighton and Hove Albion'
    assert OpenFootballProvider.normalize_team('Sunderland AFC') == 'Sunderland'


def test_full_time_score_accepts_both_openfootball_shapes():
    assert OpenFootballProvider._full_time_score([2, 1]) == [2, 1]
    assert OpenFootballProvider._full_time_score({'ft': [3, 0], 'ht': [1, 0]}) == [3, 0]
    assert OpenFootballProvider._full_time_score({'ht': [1, 0]}) is None


def test_fetch_completed_builds_training_event(monkeypatch):
    payload = {
        'matches': [
            {
                'date': '2025-08-15',
                'time': '20:00',
                'team1': 'Liverpool FC',
                'team2': 'AFC Bournemouth',
                'score': {'ft': [4, 2]},
            }
        ]
    }

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return payload

    monkeypatch.setattr('app.providers.openfootball.requests.get', lambda *args, **kwargs: Response())
    monkeypatch.setattr(OpenFootballProvider, '_season_labels', staticmethod(lambda seasons: ['2025-26']))

    rows = OpenFootballProvider().fetch_completed('soccer_epl', seasons=1)
    assert len(rows) == 1
    assert rows[0]['home_team'] == 'Liverpool'
    assert rows[0]['away_team'] == 'Bournemouth'
    assert rows[0]['completed'] is True
    assert rows[0]['scores'][0]['score'] == '4'
