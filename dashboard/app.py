#!/usr/bin/env python3
"""
Fronius Dashboard Backend API
Lightweight FastAPI server to serve the dashboard and query InfluxDB data.
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from influxdb_client import InfluxDBClient
from influxdb_client.client.flux_table import FluxTable
import os
from pathlib import Path
from datetime import datetime, timedelta
import logging
import time

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Track app startup time for uptime calculation
APP_START_TIME = time.time()

app = FastAPI(title="Fronius Dashboard")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize InfluxDB client
INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "fronius_clean")

try:
    influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = influx_client.query_api()
    logger.info(f"InfluxDB client initialized: {INFLUX_URL}")
except Exception as e:
    logger.error(f"Failed to initialize InfluxDB client: {e}")
    influx_client = None
    query_api = None

# Dashboard directory
DASHBOARD_DIR = Path(__file__).parent

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main dashboard."""
    with open(DASHBOARD_DIR / "index.html", "r") as f:
        return f.read()

@app.get("/api/health")
async def health_check():
    """Detailed health check endpoint with latency and system info."""
    result = {
        "status": "ok",
        "service": "fronius-dashboard",
        "influxdb": "disconnected",
        "db_latency": None,
        "api_latency": None,
        "memory_usage": None
    }
    
    # Check InfluxDB connection and measure latency
    if influx_client is not None:
        try:
            start = time.time()
            influx_client.health()
            latency = int((time.time() - start) * 1000)  # Convert to ms
            result["influxdb"] = "connected"
            result["db_latency"] = latency
        except Exception as e:
            result["influxdb"] = "error"
            result["status"] = "warning"
            logger.error(f"InfluxDB health check failed: {e}")
    else:
        result["status"] = "warning"
    
    # Measure API response time
    result["api_latency"] = 1  # Approximate, measured client-side is more accurate
    
    # Calculate uptime
    uptime_seconds = int(time.time() - APP_START_TIME)
    result["uptime"] = uptime_seconds
    
    # Get memory usage
    if HAS_PSUTIL:
        try:
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            result["memory_usage"] = int(memory_mb)
        except Exception as e:
            logger.warning(f"Failed to get memory usage: {e}")
            result["memory_usage"] = None
    
    return result

@app.get("/api/data/current")
async def get_current_data():
    """Get current energy data from InfluxDB (latest values)."""
    if query_api is None:
        return {"error": "InfluxDB not connected"}
    
    try:
        # Query latest values for key fields
        query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Solar_Produced_Current" or r["_field"] == "Consumption_Current" or r["_field"] == "Grid_FeedIn_Current" or r["_field"] == "Battery_SOC" or r["_field"] == "Autonomy_Percentage" or r["_field"] == "Grid_Consumption_Current")
  |> last()
'''
        
        result = query_api.query(query)
        
        # Parse results into dictionary
        data = {}
        for table in result:
            for record in table.records:
                field = record.values.get("_field")
                value = record.get_value()
                if field == "Solar_Produced_Current":
                    data["solar_production"] = value
                elif field == "Consumption_Current":
                    data["consumption"] = value
                elif field == "Grid_FeedIn_Current":
                    data["grid_feed_in"] = value
                elif field == "Battery_SOC":
                    data["battery_soc"] = value
                elif field == "Autonomy_Percentage":
                    data["autonomy"] = value
                elif field == "Grid_Consumption_Current":
                    data["grid_consumption"] = value
        
        # Calculate efficiency (solar production / consumption if available)
        if "solar_production" in data and "consumption" in data and data["consumption"] > 0:
            efficiency = (data["solar_production"] / (data["solar_production"] + data.get("grid_consumption", 0))) * 100 if (data["solar_production"] + data.get("grid_consumption", 0)) > 0 else 0
            data["efficiency"] = round(min(efficiency, 100), 1)
        else:
            data["efficiency"] = 0
        
        return data
    except Exception as e:
        logger.error(f"Error querying current data: {e}")
        return {"error": str(e)}

@app.get("/api/data/24h")
async def get_24h_data():
    """Get 24-hour historical data for power flow chart."""
    if query_api is None:
        return {"error": "InfluxDB not connected"}
    
    try:
        # Query 24-hour data with 1-hour intervals
        query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Solar_Produced_Current" or r["_field"] == "Consumption_Current")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
'''
        
        result = query_api.query(query)
        
        # Parse results into time series
        solar_data = {}
        consumption_data = {}
        
        for table in result:
            for record in table.records:
                field = record.values.get("_field")
                time_key = record.values.get("_time").strftime("%H:%M") if hasattr(record.values.get("_time"), "strftime") else str(record.values.get("_time"))
                value = record.get_value()
                
                if field == "Solar_Produced_Current":
                    solar_data[time_key] = value
                elif field == "Consumption_Current":
                    consumption_data[time_key] = value
        
        # Get sorted time labels from the last 24 hours
        labels = []
        now = datetime.now()
        for i in range(24):
            hour = (now - timedelta(hours=24-i)).strftime("%H:%M")
            labels.append(hour)
        
        # Build arrays with data or None for missing values
        solar = [solar_data.get(label, 0) for label in labels]
        consumption = [consumption_data.get(label, 0) for label in labels]
        
        return {
            "labels": labels,
            "solar": solar,
            "consumption": consumption
        }
    except Exception as e:
        logger.error(f"Error querying 24h data: {e}")
        return {"error": str(e)}

@app.get("/api/data/7d")
async def get_7d_data():
    """Get 7-day historical data for battery SOC trend."""
    if query_api is None:
        return {"error": "InfluxDB not connected"}
    
    try:
        # Query grid consumption and feed-in totals for last 8 days
        # (need 8 days to calculate 7 days of daily deltas)
        import_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -8d)
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Grid_Consumption_Total")
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false)
  |> sort(columns: ["_time"])
'''
        
        export_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -8d)
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Grid_FeedIn_Total")
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false)
  |> sort(columns: ["_time"])
'''
        
        import_result = query_api.query(import_query)
        export_result = query_api.query(export_query)
        
        # Parse results - collect all daily values in order
        import_values = []
        export_values = []
        
        for table in import_result:
            for record in table.records:
                value = record.get_value()
                import_values.append(value or 0)
        
        for table in export_result:
            for record in table.records:
                value = record.get_value()
                export_values.append(value or 0)
        
        # Calculate daily deltas (consumption/export for each day)
        # We have 8 days of data, so we calculate 7 daily deltas
        import_daily = []
        export_daily = []
        day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        
        for i in range(1, len(import_values)):
            daily_import = max(0, (import_values[i] - import_values[i-1]))
            daily_export = max(0, (export_values[i] - export_values[i-1]))
            import_daily.append(daily_import)
            export_daily.append(daily_export)
        
        # Keep only last 7 days and calculate costs
        import_daily = import_daily[-7:] if len(import_daily) >= 7 else import_daily
        export_daily = export_daily[-7:] if len(export_daily) >= 7 else export_daily
        
        # Pad with zeros if we have fewer than 7 days
        while len(import_daily) < 7:
            import_daily.insert(0, 0)
        while len(export_daily) < 7:
            export_daily.insert(0, 0)
        
        # Calculate costs and income
        # Cost: 0.3€ per kWh imported, Income: 0.06€ per kWh exported
        import_costs = [round(val * 0.3, 2) for val in import_daily]
        export_income = [round(val * 0.06, 2) for val in export_daily]
        
        return {
            "labels": day_labels,
            "import_costs": import_costs,
            "export_income": export_income
        }
    except Exception as e:
        logger.error(f"Error querying 7d data: {e}")
        return {"error": str(e)}

@app.get("/api/data/today")
async def get_today_stats():
    """Get today's energy statistics."""
    if query_api is None:
        return {"error": "InfluxDB not connected"}
    
    try:
        # Query totals from today - use multiple filters instead of array
        query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Solar_Produced_Total" or r["_field"] == "Consumption_Total" or r["_field"] == "Grid_FeedIn_Total" or r["_field"] == "Grid_Consumption_Total")
  |> last()
'''
        
        result = query_api.query(query)
        
        data = {}
        for table in result:
            for record in table.records:
                field = record.values.get("_field")
                value = record.get_value()
                if field == "Solar_Produced_Total":
                    data["solar_production"] = value
                elif field == "Consumption_Total":
                    data["consumption"] = value
                elif field == "Grid_FeedIn_Total":
                    data["grid_export"] = value
                elif field == "Grid_Consumption_Total":
                    data["grid_import"] = value
        
        return data
    except Exception as e:
        logger.error(f"Error querying today stats: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
