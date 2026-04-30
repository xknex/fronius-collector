#!/usr/bin/env python3
"""
Fronius Dashboard Backend API
Lightweight FastAPI server to serve the dashboard and query InfluxDB data.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from pathlib import Path

app = FastAPI(title="Fronius Dashboard")

# Add CORS middleware for cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (CSS, JS, etc.)
DASHBOARD_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main dashboard."""
    with open(DASHBOARD_DIR / "index.html", "r") as f:
        return f.read()

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "fronius-dashboard"}

@app.get("/api/data/current")
async def get_current_data():
    """
    Get current energy data from InfluxDB.
    This is a placeholder - integrate with your InfluxDB instance.
    """
    # TODO: Query InfluxDB for latest data
    return {
        "solar_production": 6.84,
        "consumption": 3.92,
        "grid_feed_in": 2.15,
        "battery_soc": 62.3,
        "autonomy": 89,
        "efficiency": 94.2
    }

@app.get("/api/data/24h")
async def get_24h_data():
    """
    Get 24-hour historical data.
    This is a placeholder - integrate with your InfluxDB instance.
    """
    # TODO: Query InfluxDB for 24-hour data
    return {
        "labels": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "24:00"],
        "solar": [0.1, 0.2, 2.5, 6.8, 5.2, 1.2, 0.1],
        "consumption": [1.2, 0.8, 1.5, 3.2, 4.1, 3.8, 1.5]
    }

@app.get("/api/data/7d")
async def get_7d_data():
    """
    Get 7-day historical data.
    This is a placeholder - integrate with your InfluxDB instance.
    """
    # TODO: Query InfluxDB for 7-day data
    return {
        "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "soc": [45, 52, 58, 65, 70, 72, 62]
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
