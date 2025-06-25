from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from contextlib import asynccontextmanager
from lotto import update_draws, update_statistics, get_heights_by_draw_id
from db.models import *
import logging
import os

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.addHandler(DatabaseHandler(level=logging.INFO))

# Import necessary modules for scheduling
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_draws, IntervalTrigger(minutes=10))
    scheduler.start()
    logger.info("Scheduled task started, checking for new blocks every 10 minutes")
    
    yield  # Application running
    
    # Shutdown logic
    scheduler.shutdown()
    logger.info("Scheduled task stopped")

app = FastAPI(lifespan=lifespan)

# Mount templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Ensure the static directory exists
if not os.path.exists("static"):
    os.makedirs("static")

# Initialize the database
init_db()

# CORS configuration
origins = [
    "http://localhost",
    "http://localhost:3000",
    "https://bit-lotto.vercel.app",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def index(request: Request):
    # return {"Hello": "World"}
    """Homepage: Display the latest 20 lottery draws"""
    recent_draws = get_limit_draws()
    last_draw_height = recent_draws[0].end_height if recent_draws else 0
    current_height = get_max_bitcoin_height()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "draws": recent_draws,
        "num_trials": get_max_draw_id() + 1,
        "last_draw_height": last_draw_height,
        "current_height": current_height
    })

@app.get("/draw/{trial_id}")
async def get_draw(request: Request, trial_id: int):
    """Display the lottery numbers for the specified draw"""
    draw = get_draw_by_id(trial_id)
    heights = get_heights_by_draw_id(trial_id)
    bitcoins = select_bitcoin_by_height(heights)
    if draw:
        return templates.TemplateResponse("draw.html", {
            "request": request,
            "bitcoins": bitcoins,
            "draw": draw
        })
    return {"error": "Invalid draw number"}

@app.get("/stats")
async def stats(request: Request):
    """Display chi-square test results and frequency distribution chart"""
    statistics = get_last_statistics()
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "statistics": statistics
    })

@app.get("/trigger-draw")
async def trigger_draw(background_tasks: BackgroundTasks):
    background_tasks.add_task(update_draws)
    return {"message": "The draw has been triggered, please check the result later"}

@app.get("/refresh-statistics")
async def refresh_statistics(background_tasks: BackgroundTasks):
    background_tasks.add_task(update_statistics)
    return {"message": "The statistics has been refreshed, please check the result later"}

@app.get("/logs")
async def logs(request: Request, page: int = 1):
    logs, total_pages = get_log_entries(page=page)
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "logs": logs,
        "page": page,
        "total_pages": total_pages
    })

@app.get("/api/index")
async def api_index():
    """API endpoint to get the latest 20 lottery draws"""
    recent_draws = get_limit_draws()
    last_draw_height = recent_draws[0].end_height if recent_draws else 0
    current_height = get_max_bitcoin_height()
    return {
        "draws": recent_draws,
        "num_trials": get_max_draw_id() + 1,
        "last_draw_height": last_draw_height,
        "current_height": current_height
    }

@app.get("/api/draws")
async def api_draws():
    """API endpoint to get all lottery draws"""
    draws = get_all_draws()
    return {"draws": draws}

@app.get("/api/draw/{trial_id}")
async def api_get_draw(trial_id: int):
    """API endpoint to get a specific lottery draw by trial ID"""
    draw = get_draw_by_id(trial_id)
    heights = get_heights_by_draw_id(trial_id)
    bitcoins = select_bitcoin_by_height(heights)
    if draw:
        return {
            "bitcoins": bitcoins,
            "draw": draw
        }
    return {"error": "Invalid draw number"}