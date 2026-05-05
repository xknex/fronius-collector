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
from datetime import datetime, timedelta, timezone
import logging
import time

# Load environment variables from .env file (for local development)
# In Docker, environment variables should be passed via docker-compose env_file and environment
try:
    from dotenv import load_dotenv
    
    # Try multiple paths
    possible_paths = [
        Path(__file__).parent.parent / ".env",  # /app/.env in container, or parent in local dev
        Path(__file__).parent / ".env",         # /app/dashboard/.env
        Path.cwd() / ".env",                    # Current working directory
    ]
    
    loaded = False
    for env_path in possible_paths:
        if env_path.exists():
            load_dotenv(env_path)
            print(f"✓ Loaded .env file from: {env_path}")
            loaded = True
            break
    
    if not loaded:
        print(f"ℹ No .env file found. Using environment variables from docker-compose or system.")
        print(f"  Checked paths: {[str(p) for p in possible_paths]}")
        
except ImportError:
    print("ℹ python-dotenv not installed, using environment variables passed by docker-compose or system")

# Try to use zoneinfo (Python 3.9+), fall back to pytz
try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    try:
        import pytz
        HAS_ZONEINFO = False
    except ImportError:
        HAS_ZONEINFO = None

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get timezone from environment or use system default
TIMEZONE = os.getenv("TZ", "UTC")

logger.info(f"Checking TZ environment variable...")
logger.info(f"TZ from os.getenv(): {TIMEZONE}")
logger.info(f"Full environment TZ: {os.environ.get('TZ', 'NOT SET')}")

# Set timezone for the application
if TIMEZONE != "UTC":
    os.environ['TZ'] = TIMEZONE
    try:
        import time as time_module
        time_module.tzset()
        logger.info(f"Successfully set TZ={TIMEZONE} and called tzset()")
    except AttributeError:
        logger.info(f"tzset() not available on this platform, TZ set to {TIMEZONE}")
else:
    logger.warning(f"No TZ environment variable found, defaulting to UTC")

logger.info(f"Application timezone: {TIMEZONE}")

# Create timezone object once at startup for better performance
try:
    if HAS_ZONEINFO:
        LOCAL_TZ = ZoneInfo(TIMEZONE)
        logger.info(f"Using ZoneInfo for timezone: {TIMEZONE}")
    elif HAS_ZONEINFO is False:
        LOCAL_TZ = pytz.timezone(TIMEZONE)
        logger.info(f"Using pytz for timezone: {TIMEZONE}")
    else:
        LOCAL_TZ = None
        logger.warning(f"No timezone library available, cannot set timezone to {TIMEZONE}")
except Exception as e:
    logger.error(f"Failed to initialize timezone {TIMEZONE}: {e}")
    LOCAL_TZ = None

# Helper function to convert UTC timestamps to local timezone
def to_local_time(dt):
    """Convert UTC datetime to local timezone using the TZ environment variable."""
    if dt is None:
        return dt
    
    # If datetime is naive, assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # Use pre-initialized timezone object
    if LOCAL_TZ:
        try:
            result = dt.astimezone(LOCAL_TZ)
            logger.debug(f"Converted {dt} -> {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to convert timezone using {TIMEZONE}: {e}")
    
    # Fall back to system default
    logger.debug(f"Using system default timezone for {dt}")
    return dt.astimezone()

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
        "memory_usage": None,
        "timezone": TIMEZONE,
        "timestamp": datetime.now().isoformat()
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

@app.get("/api/config/timezone")
async def get_timezone_config():
    """Get server timezone configuration."""
    return {
        "timezone": TIMEZONE,
        "server_time": to_local_time(datetime.now(timezone.utc)).isoformat()
    }

@app.get("/api/debug/timezone")
async def debug_timezone():
    """Debug endpoint to show timezone conversion."""
    now_utc = datetime.now(timezone.utc)
    now_local = to_local_time(now_utc)
    
    return {
        "environment_tz": TIMEZONE,
        "utc_now": now_utc.isoformat(),
        "utc_now_str": now_utc.strftime("%H:%M:%S"),
        "local_now": now_local.isoformat(),
        "local_now_str": now_local.strftime("%H:%M:%S"),
        "timezone_info": {
            "tzinfo": str(now_local.tzinfo),
            "utc_offset": str(now_local.utcoffset()),
            "dst": str(now_local.dst())
        }
    }

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

@app.get("/api/data/power")
async def get_power_data(range: str = "24h"):
    """Get power flow data with configurable time range.
    
    Supported ranges: 3h, 12h, 24h, 7d
    Automatically adjusts aggregation window for optimal visualization.
    """
    if query_api is None:
        return {"error": "InfluxDB not connected"}
    
    # Define range parameters: (query_range, agg_window, points_expected)
    range_config = {
        "3h": ("-3h", "5m", 36),
        "12h": ("-12h", "15m", 48),
        "24h": ("-24h", "30m", 48),
        "7d": ("-7d", "4h", 42)
    }
    
    if range not in range_config:
        return {"error": f"Invalid range. Supported: {', '.join(range_config.keys())}"}
    
    query_range, agg_window, expected_points = range_config[range]
    
    try:
        # Query power data with appropriate aggregation
        query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {query_range})
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Solar_Produced_Current" or r["_field"] == "Consumption_Current")
  |> aggregateWindow(every: {agg_window}, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
'''
        
        result = query_api.query(query)
        
        # Parse results into time series
        solar_data = {}
        consumption_data = {}
        timestamps = []
        
        for table in result:
            for record in table.records:
                field = record.values.get("_field")
                timestamp = record.values.get("_time")
                value = record.get_value()
                
                # Convert to local time
                local_timestamp = to_local_time(timestamp)
                
                # Format time based on range
                if range == "3h":
                    time_key = local_timestamp.strftime("%H:%M")
                elif range == "12h":
                    time_key = local_timestamp.strftime("%H:%M")
                elif range == "24h":
                    time_key = local_timestamp.strftime("%H:%M")
                else:  # 7d
                    time_key = local_timestamp.strftime("%a %H:%M")
                
                if field == "Solar_Produced_Current":
                    solar_data[timestamp] = value
                elif field == "Consumption_Current":
                    consumption_data[timestamp] = value
                
                if timestamp not in timestamps:
                    timestamps.append(timestamp)
        
        # Sort timestamps
        timestamps.sort()
        
        # Generate labels based on timestamps
        labels = []
        if range == "3h":
            labels = [to_local_time(t).strftime("%H:%M") for t in timestamps]
        elif range == "12h":
            labels = [to_local_time(t).strftime("%H:%M") for t in timestamps]
        elif range == "24h":
            labels = [to_local_time(t).strftime("%H:%M") for t in timestamps]
        else:  # 7d
            labels = [to_local_time(t).strftime("%a %H:%M") for t in timestamps]
        
        # Build data arrays
        solar = [solar_data.get(t, 0) for t in timestamps]
        consumption = [consumption_data.get(t, 0) for t in timestamps]
        
        # Log for debugging
        if labels:
            logger.info(f"Power data for range={range}: first_label={labels[0]}, last_label={labels[-1]}, points={len(labels)}")
            if timestamps:
                logger.debug(f"First timestamp UTC: {timestamps[0]}, Local: {to_local_time(timestamps[0])}")
                logger.debug(f"Last timestamp UTC: {timestamps[-1]}, Local: {to_local_time(timestamps[-1])}")
        
        return {
            "labels": labels,
            "solar": solar,
            "consumption": consumption,
            "range": range,
            "points": len(timestamps)
        }
    except Exception as e:
        logger.error(f"Error querying power data for range {range}: {e}", exc_info=True)
        return {"error": str(e)}

@app.get("/api/data/24h")
async def get_24h_data():
    """Get 24-hour historical data for power flow chart. (Legacy endpoint - use /api/data/power?range=24h)"""
    return await get_power_data(range="24h")

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
