from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.config import get_settings
from app.core.db import Base, engine, SessionLocal
from app.api.routes import router

settings = get_settings()
BASE = Path(__file__).resolve().parent
app = FastAPI(title=settings.app_name, version='1.0.0')
app.include_router(router)
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
templates = Jinja2Templates(directory=BASE/'templates')
scheduler = BackgroundScheduler(timezone='UTC')


@app.on_event('startup')
def startup():
    Base.metadata.create_all(bind=engine)
    # Scheduler is intentionally not auto-fetching without an API key.
    # Use /api/run-cycle manually first; enable recurring jobs after validating data quality.


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse('dashboard.html', {'request': request, 'app_name': settings.app_name})
