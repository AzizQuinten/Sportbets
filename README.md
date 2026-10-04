# SportBet Edge Lab V1

A **paper-only, price-first sports betting research engine** designed to answer one question: do we have a repeatable edge after bookmaker margin, line movement, stake sizing and variance?

## What V1 already does

- Fetches live/upcoming bookmaker prices from **The Odds API**.
- Normalizes bookmaker prices and removes vig per book.
- Builds a robust median market consensus instead of trusting one sportsbook.
- Shops for the best available price.
- Scores edge, EV, market dispersion, vig and confidence.
- Uses fractional Kelly with hard caps for paper stake sizing.
- Enforces per-bet, per-event and per-day exposure limits.
- Saves raw odds snapshots, signals and a full paper-bet ledger.
- Settles H2H paper bets from score data.
- Includes a Railway-ready FastAPI dashboard.
- Defaults to `PAPER_ONLY=true`; there is **no bookmaker bet-placement integration**.

## Why the model is intentionally conservative

The most dangerous V1 is a model that invents confidence before it has training data. This starter therefore anchors probabilities to de-vigged multi-book consensus. The model hook in `app/services/model.py` is where a properly time-split and calibrated Elo/Poisson/XGBoost ensemble should be added once historical data exists.

Do **not** optimize only for hit-rate. The important research metrics are out-of-sample log loss/Brier score, calibration, closing-line value (CLV), realized ROI after enough bets, maximum drawdown and stability by league/market/odds bucket.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

Add your `ODDS_API_KEY` to `.env`, then:

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000` and press **Run scan**.

## Railway

Push the project to GitHub, create a Railway service from the repo, add the variables from `.env.example`, and preferably attach Railway Postgres. Set `DATABASE_URL` to the Railway Postgres URL. The included `railway.toml` starts Uvicorn and uses `/health` as its healthcheck.

## Recommended first scope

Start narrow: soccer `h2h` (1X2) in liquid leagues. Do not add props, live betting and dozens of competitions until the core pipeline proves itself. More markets create more false discoveries, not automatically more edge.

## Research roadmap

### V1 — instrumentation (this package)
Collect every price snapshot and paper bet. Verify timestamps, bookmaker mapping, event matching, score settlement and risk limits.

### V1.5 — historical dataset
Use historical odds snapshots plus match/team features. Freeze a clean event-time dataset that prevents future leakage.

### V2 — predictive ensemble
Add:
- dynamic team Elo with home advantage and recency decay;
- Poisson/Dixon-Coles score model for soccer;
- gradient-boosted model on form, schedule, team strength and line features;
- strict walk-forward training;
- isotonic/Platt probability calibration;
- ensemble weights learned only on prior data.

### V2.5 — market intelligence
Add line velocity, stale-book detection, sharp-vs-soft book deltas, consensus breadth, price persistence, steam/reversal tags and closing-line capture.

### V3 — portfolio/risk engine
Add correlated-bet controls, league/market exposure budgets, dynamic bankroll, drawdown throttles, Monte Carlo risk-of-ruin and automatic strategy kill-switches.

### V4 — research promotion gates
A strategy is promoted only if it beats explicit minimums in an untouched evaluation window: positive CLV, acceptable calibration, enough sample size, stable edge by time period and tolerable drawdown. Live wagering should remain a separate, explicit decision.

## Important modeling rules

1. **Never train on closing odds if the prediction is supposed to be made earlier.** That is leakage.
2. **Split by time, never random train/test split** for market prediction.
3. Track the exact odds available at the exact decision timestamp.
4. Compare against a market baseline; a fancy model that cannot beat consensus log loss is not useful.
5. Penalize multiple testing. Trying 500 filters until one backtest wins is overfitting.
6. Calibrate probabilities. 60% predictions should win about 60% over a large sample.
7. Judge price quality using **CLV**, not just short-term P/L.

## Current limitation

V1 cannot honestly claim predictive alpha before historical data has been collected/trained. That is deliberate. The project starts with the plumbing, risk discipline and auditability needed to determine whether later models actually add value.
