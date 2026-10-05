from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.config import get_settings
from app.core.db import Base, engine
from app.api.routes import router
from app.services.cycle import auto_scan_tick, settle_if_needed, refresh_model_tick, bootstrap_model_tick

APP_VERSION = '2.3.0'
settings = get_settings()
BASE = Path(__file__).resolve().parent
app = FastAPI(title=settings.app_name, version=APP_VERSION)
app.include_router(router)
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
templates = Jinja2Templates(directory=BASE / 'templates')
scheduler = BackgroundScheduler(timezone='UTC')

@app.on_event('startup')
def startup():
    Base.metadata.create_all(bind=engine)
    if settings.auto_scan_enabled and settings.odds_api_key:
        scheduler.add_job(auto_scan_tick,'interval',minutes=max(1, settings.poll_minutes),id='adaptive-auto-scan',replace_existing=True,max_instances=1,coalesce=True)
    if settings.historical_bootstrap_enabled or settings.odds_api_key:
        scheduler.add_job(bootstrap_model_tick,'date',run_date=datetime.now(timezone.utc)+timedelta(seconds=15),id='initial-model-bootstrap',replace_existing=True)
    if settings.odds_api_key:
        scheduler.add_job(refresh_model_tick,'interval',hours=max(1, settings.model_refresh_hours),id='model-learning-refresh',replace_existing=True,max_instances=1,coalesce=True)
    if settings.auto_settle_enabled and settings.odds_api_key:
        scheduler.add_job(settle_if_needed,'interval',minutes=60,id='auto-settle',replace_existing=True,max_instances=1,coalesce=True)
    if not scheduler.running and scheduler.get_jobs(): scheduler.start()

@app.on_event('shutdown')
def shutdown():
    if scheduler.running: scheduler.shutdown(wait=False)

@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse('dashboard.html', {'request': request, 'app_name': settings.app_name, 'app_version': APP_VERSION})
