from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings
from app.core.db import Base, engine
from app.api.routes import router
from app.services.cycle import auto_scan_tick, settle_if_needed

settings = get_settings()
BASE = Path(__file__).resolve().parent
app = FastAPI(title=settings.app_name, version='1.1.0')
app.include_router(router)
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
templates = Jinja2Templates(directory=BASE/'templates')
scheduler = BackgroundScheduler(timezone='UTC')


@app.on_event('startup')
def startup():
    Base.metadata.create_all(bind=engine)

    # Wake frequently but let cycle.py decide whether a real API scan is due.
    # This gives us near-kickoff responsiveness without burning a small quota.
    if settings.auto_scan_enabled and settings.odds_api_key:
        scheduler.add_job(
            auto_scan_tick,
            'interval',
            minutes=max(1, settings.poll_minutes),
            id='adaptive-auto-scan',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    if settings.auto_settle_enabled and settings.odds_api_key:
        scheduler.add_job(
            settle_if_needed,
            'interval',
            minutes=60,
            id='auto-settle',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    if not scheduler.running and scheduler.get_jobs():
        scheduler.start()


@app.on_event('shutdown')
def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse('dashboard.html', {'request': request, 'app_name': settings.app_name})
