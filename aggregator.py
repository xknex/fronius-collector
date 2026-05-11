#!/usr/bin/env python3
"""
Fronius Aggregation Service

Pre-computes daily, weekly, monthly, and annual energy totals and peak power values
from raw InfluxDB data. The dashboard then queries these pre-aggregated measurements
instead of millions of raw 10-second data points.

Measurements written:
  - fronius_agg_daily   (one point per calendar day)
  - fronius_agg_weekly  (one point per ISO week)
  - fronius_agg_monthly (one point per calendar month)
  - fronius_agg_annual  (one point per calendar year)
"""

import os
import sys
import time
import signal
import argparse
import calendar
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Dict, Any

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Timezone setup
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    try:
        import pytz
        HAS_ZONEINFO = False
    except ImportError:
        HAS_ZONEINFO = None


def get_local_timezone():
    """Return a timezone object for the configured TZ environment variable."""
    tz_name = os.getenv("TZ", "UTC")
    if HAS_ZONEINFO:
        return ZoneInfo(tz_name)
    elif HAS_ZONEINFO is False:
        return pytz.timezone(tz_name)
    else:
        return timezone.utc


LOCAL_TZ = get_local_timezone()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config:
    """Aggregation service configuration loaded from environment variables."""

    def __init__(self):
        # InfluxDB connection (same vars as collector)
        self.influx_url = os.getenv("INFLUX_URL", "http://localhost:8086")
        self.influx_token = os.getenv("INFLUX_TOKEN", "")
        self.influx_org = os.getenv("INFLUX_ORG", "org")
        self.influx_bucket = os.getenv("INFLUX_BUCKET", "fronius")

        # Tags (same as collector)
        self.tag_source = os.getenv("TAG_SOURCE", "SymoGEN24")
        self.tag_site = os.getenv("TAG_SITE", "home")

        # Aggregation schedule (local time)
        self.schedule_hour = int(os.getenv("AGGREGATION_SCHEDULE_HOUR", "0"))
        self.schedule_minute = int(os.getenv("AGGREGATION_SCHEDULE_MINUTE", "15"))

        # Measurement prefix
        self.prefix = os.getenv("AGGREGATION_MEASUREMENT_PREFIX", "fronius_agg")

    @property
    def measurement_daily(self) -> str:
        return f"{self.prefix}_daily"

    @property
    def measurement_weekly(self) -> str:
        return f"{self.prefix}_weekly"

    @property
    def measurement_monthly(self) -> str:
        return f"{self.prefix}_monthly"

    @property
    def measurement_annual(self) -> str:
        return f"{self.prefix}_annual"

    def validate(self):
        """Validate critical configuration. Exits on failure."""
        if not self.influx_token:
            log_error("INFLUX_TOKEN is not set")
            sys.exit(1)
        if not self.influx_url:
            log_error("INFLUX_URL is not set")
            sys.exit(1)
        if not self.influx_bucket:
            log_error("INFLUX_BUCKET is not set")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d/%H:%M:%S")


def log_info(msg: str):
    print(f"{_ts()} [AGG/INFO] {msg}", flush=True)


def log_warn(msg: str):
    print(f"{_ts()} [AGG/WARN] {msg}", flush=True)


def log_error(msg: str):
    print(f"{_ts()} [AGG/ERROR] {msg}", file=sys.stderr, flush=True)


def log_debug(msg: str):
    if os.getenv("AGG_DEBUG", "").lower() in ("1", "true", "yes"):
        print(f"{_ts()} [AGG/DEBUG] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def local_day_to_utc_range(d: date) -> tuple:
    """Convert a local calendar date to (start_utc, stop_utc) datetime pair.

    Returns UTC datetimes representing the local day boundaries.
    """
    local_midnight = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=LOCAL_TZ)
    next_midnight = local_midnight + timedelta(days=1)
    return (local_midnight.astimezone(timezone.utc), next_midnight.astimezone(timezone.utc))


def today_local() -> date:
    """Return today's date in the configured local timezone."""
    return datetime.now(LOCAL_TZ).date()


def yesterday_local() -> date:
    """Return yesterday's date in the configured local timezone."""
    return today_local() - timedelta(days=1)


# ---------------------------------------------------------------------------
# InfluxDB connection with retry
# ---------------------------------------------------------------------------

MAX_RETRY_DELAY = 300  # 5 minutes
INITIAL_RETRY_DELAY = 5  # 5 seconds


def connect_influxdb(cfg: Config) -> InfluxDBClient:
    """Connect to InfluxDB with exponential backoff retry."""
    delay = INITIAL_RETRY_DELAY
    while True:
        try:
            client = InfluxDBClient(
                url=cfg.influx_url,
                token=cfg.influx_token,
                org=cfg.influx_org
            )
            # Verify connection
            health = client.health()
            if health.status == "pass":
                log_info(f"Connected to InfluxDB at {cfg.influx_url}")
                return client
            else:
                raise ConnectionError(f"InfluxDB health status: {health.status}")
        except Exception as e:
            log_warn(f"InfluxDB connection failed: {e}. Retrying in {delay}s...")
            time.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    log_info(f"Received signal {signum}, shutting down gracefully...")
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def shutdown_requested() -> bool:
    return _shutdown_requested


# ---------------------------------------------------------------------------
# Daily Aggregation
# ---------------------------------------------------------------------------

def aggregate_day(d: date, client: InfluxDBClient, cfg: Config) -> Optional[Dict[str, float]]:
    """Aggregate raw data for a single calendar day.

    Queries fronius_clean with spread() for cumulative energy totals and
    max() for instantaneous power fields.

    Args:
        d: The local calendar date to aggregate.
        client: Connected InfluxDB client.
        cfg: Service configuration.

    Returns:
        A dict with grid_import_kwh, grid_export_kwh, and all peak values,
        or None if no raw data exists for that day.
    """
    start_utc, stop_utc = local_day_to_utc_range(d)
    query_api = client.query_api()

    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Query 1: spread() for cumulative energy totals ---
    spread_query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Grid_Consumption_Total" or
                        r["_field"] == "Grid_FeedIn_Total")
  |> spread()
'''

    # --- Query 2: max() for instantaneous power fields ---
    peak_fields = [
        "Solar_Produced_Current",
        "Consumption_Current",
        "Grid_FeedIn_Current",
        "Grid_Consumption_Current",
        "Battery_Charging",
        "Battery_Discharging",
    ]
    peak_filter = " or\n                        ".join(
        [f'r["_field"] == "{f}"' for f in peak_fields]
    )
    max_query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => {peak_filter})
  |> max()
'''

    try:
        # Execute spread query for energy totals
        spread_tables = query_api.query(spread_query, org=cfg.influx_org)
        energy_data = {}
        for table in spread_tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                if field == "Grid_Consumption_Total":
                    energy_data["grid_import_kwh"] = float(value)
                elif field == "Grid_FeedIn_Total":
                    energy_data["grid_export_kwh"] = float(value)

        # If no energy data found, no raw data exists for this day
        if not energy_data:
            log_debug(f"No raw data for {d}, skipping.")
            return None

        # Execute max query for peak power values
        peak_tables = query_api.query(max_query, org=cfg.influx_org)
        peak_data = {
            "peak_solar_kw": 0.0,
            "peak_consumption_kw": 0.0,
            "peak_grid_feedin_kw": 0.0,
            "peak_grid_consumption_kw": 0.0,
            "peak_battery_charging_kw": 0.0,
            "peak_battery_discharging_kw": 0.0,
        }

        field_to_key = {
            "Solar_Produced_Current": "peak_solar_kw",
            "Consumption_Current": "peak_consumption_kw",
            "Grid_FeedIn_Current": "peak_grid_feedin_kw",
            "Grid_Consumption_Current": "peak_grid_consumption_kw",
            "Battery_Charging": "peak_battery_charging_kw",
            "Battery_Discharging": "peak_battery_discharging_kw",
        }

        for table in peak_tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                if field in field_to_key:
                    peak_data[field_to_key[field]] = float(value)

        # Combine energy and peak data
        result = {**energy_data, **peak_data}

        # Ensure both energy fields are present (default to 0 if one is missing)
        result.setdefault("grid_import_kwh", 0.0)
        result.setdefault("grid_export_kwh", 0.0)

        log_debug(f"Aggregated {d}: import={result['grid_import_kwh']:.2f} kWh, "
                  f"export={result['grid_export_kwh']:.2f} kWh")
        return result

    except Exception as e:
        log_error(f"Failed to aggregate day {d}: {e}")
        raise


def write_daily_aggregation(d: date, data: Dict[str, float], client: InfluxDBClient, cfg: Config) -> None:
    """Write a single aggregated daily data point to InfluxDB.

    Args:
        d: The calendar date this aggregation represents.
        data: Dict with keys: grid_import_kwh, grid_export_kwh, peak_solar_kw,
              peak_consumption_kw, peak_grid_feedin_kw, peak_grid_consumption_kw,
              peak_battery_charging_kw, peak_battery_discharging_kw.
        client: Connected InfluxDB client.
        cfg: Service configuration.
    """
    timestamp = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    point = (
        Point(cfg.measurement_daily)
        .tag("source", cfg.tag_source)
        .tag("site", cfg.tag_site)
        .field("grid_import_kwh", data["grid_import_kwh"])
        .field("grid_export_kwh", data["grid_export_kwh"])
        .field("peak_solar_kw", data["peak_solar_kw"])
        .field("peak_consumption_kw", data["peak_consumption_kw"])
        .field("peak_grid_feedin_kw", data["peak_grid_feedin_kw"])
        .field("peak_grid_consumption_kw", data["peak_grid_consumption_kw"])
        .field("peak_battery_charging_kw", data["peak_battery_charging_kw"])
        .field("peak_battery_discharging_kw", data["peak_battery_discharging_kw"])
        .time(timestamp, WritePrecision.S)
    )

    write_api = client.write_api(write_options=SYNCHRONOUS)
    write_api.write(bucket=cfg.influx_bucket, org=cfg.influx_org, record=point)

    log_info(f"Wrote daily aggregation for {d} to {cfg.measurement_daily}")


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------

def get_first_raw_data_date(client: InfluxDBClient, cfg: Config) -> Optional[date]:
    """Query fronius_clean for the earliest timestamp and return it as a local date.

    Uses Flux first() to find the oldest data point in the raw measurement.
    Converts the UTC timestamp to the configured local timezone and returns
    the date portion.

    Args:
        client: Connected InfluxDB client.
        cfg: Service configuration.

    Returns:
        The local date of the earliest raw data point, or None if no data exists.
    """
    query_api = client.query_api()

    query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> first()
  |> keep(columns: ["_time"])
'''

    try:
        tables = query_api.query(query, org=cfg.influx_org)

        for table in tables:
            for record in table.records:
                utc_time = record.get_time()
                # Convert UTC timestamp to local timezone
                local_time = utc_time.astimezone(LOCAL_TZ)
                log_debug(f"First raw data timestamp: {utc_time} (local: {local_time})")
                return local_time.date()

        # No records found
        log_info("No raw data found in fronius_clean.")
        return None

    except Exception as e:
        log_error(f"Failed to query first raw data date: {e}")
        raise


def get_last_aggregated_date(client: InfluxDBClient, cfg: Config) -> Optional[date]:
    """Query fronius_agg_daily for the latest timestamp and return it as a local date.

    Uses Flux last() to find the most recent data point in the daily aggregated
    measurement. Converts the UTC timestamp to the configured local timezone and
    returns the date portion.

    Args:
        client: Connected InfluxDB client.
        cfg: Service configuration.

    Returns:
        The local date of the latest aggregated data point, or None if no
        aggregated data exists yet (first run).
    """
    query_api = client.query_api()

    query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_daily}")
  |> last()
  |> keep(columns: ["_time"])
'''

    try:
        tables = query_api.query(query, org=cfg.influx_org)

        for table in tables:
            for record in table.records:
                utc_time = record.get_time()
                # Convert UTC timestamp to local timezone
                local_time = utc_time.astimezone(LOCAL_TZ)
                log_debug(f"Last aggregated timestamp: {utc_time} (local: {local_time})")
                return local_time.date()

        # No records found — first run
        log_info("No aggregated data found in {}.".format(cfg.measurement_daily))
        return None

    except Exception as e:
        log_error(f"Failed to query last aggregated date: {e}")
        raise


def backfill_daily(client: InfluxDBClient, cfg: Config) -> None:
    """Backfill missing daily aggregations from first raw data date to yesterday.

    Determines the range of missing days by checking the last aggregated date
    (resume point) or falling back to the first raw data date. Iterates
    chronologically, computing and writing daily aggregations.

    Handles InfluxDB errors with exponential backoff retry (5s → 5min max).
    Logs progress every 30 days processed.

    Args:
        client: Connected InfluxDB client.
        cfg: Service configuration.
    """
    # Determine start date
    first_raw = get_first_raw_data_date(client, cfg)
    if first_raw is None:
        log_info("No raw data available. Nothing to backfill.")
        return

    last_aggregated = get_last_aggregated_date(client, cfg)

    if last_aggregated is not None:
        start_date = last_aggregated + timedelta(days=1)
    else:
        start_date = first_raw

    # End at yesterday (local time)
    end_date = date.today() - timedelta(days=1)

    if start_date > end_date:
        log_info("Daily backfill is up to date. Nothing to do.")
        return

    total_days = (end_date - start_date).days + 1
    log_info(f"Starting daily backfill: {start_date} to {end_date} ({total_days} days)")

    days_processed = 0
    current_date = start_date

    while current_date <= end_date:
        if shutdown_requested():
            log_info(f"Shutdown requested during backfill. Processed {days_processed}/{total_days} days.")
            return

        # Retry loop with exponential backoff for InfluxDB errors
        delay = INITIAL_RETRY_DELAY
        while True:
            try:
                result = aggregate_day(current_date, client, cfg)
                if result is not None:
                    write_daily_aggregation(current_date, result, client, cfg)
                else:
                    log_debug(f"No data for {current_date}, skipping.")
                break  # Success — exit retry loop
            except Exception as e:
                log_warn(f"Error processing {current_date}: {e}. Retrying in {delay}s...")
                time.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY)

        days_processed += 1

        # Log progress every 30 days
        if days_processed % 30 == 0:
            log_info(f"Backfill progress: {days_processed}/{total_days} days processed")

        current_date += timedelta(days=1)

    log_info(f"Daily backfill complete: {days_processed} days processed.")


# ---------------------------------------------------------------------------
# Rollup functions
# ---------------------------------------------------------------------------

def rollup_weekly(monday: date, client: InfluxDBClient, cfg: Config) -> None:
    """Compute weekly rollup from daily aggregations for a given ISO week.

    Queries fronius_agg_daily for the 7-day range starting from the given Monday,
    sums energy fields and takes the maximum of each peak field. Writes a single
    point to fronius_agg_weekly.

    Args:
        monday: The Monday (start) of the ISO week to roll up.
        client: Connected InfluxDB client.
        cfg: Service configuration.
    """
    # Define the 7-day UTC range: Monday 00:00 UTC to next Monday 00:00 UTC
    start_utc = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    next_monday = monday + timedelta(days=7)
    stop_utc = datetime(next_monday.year, next_monday.month, next_monday.day, tzinfo=timezone.utc)

    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    query_api = client.query_api()

    # Query all fields from fronius_agg_daily for this week
    query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_daily}")
  |> filter(fn: (r) => r["_field"] == "grid_import_kwh" or
                        r["_field"] == "grid_export_kwh" or
                        r["_field"] == "peak_solar_kw" or
                        r["_field"] == "peak_consumption_kw" or
                        r["_field"] == "peak_grid_feedin_kw" or
                        r["_field"] == "peak_grid_consumption_kw" or
                        r["_field"] == "peak_battery_charging_kw" or
                        r["_field"] == "peak_battery_discharging_kw")
'''

    try:
        tables = query_api.query(query, org=cfg.influx_org)

        # Collect values per field
        field_values: Dict[str, list] = {}
        for table in tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                if value is not None:
                    field_values.setdefault(field, []).append(float(value))

        # If no daily data exists for this week, skip
        if not field_values:
            log_debug(f"No daily data for week starting {monday}, skipping weekly rollup.")
            return

        # Sum energy fields
        grid_import_kwh = sum(field_values.get("grid_import_kwh", [0.0]))
        grid_export_kwh = sum(field_values.get("grid_export_kwh", [0.0]))

        # Max of peak fields
        peak_solar_kw = max(field_values.get("peak_solar_kw", [0.0]))
        peak_consumption_kw = max(field_values.get("peak_consumption_kw", [0.0]))
        peak_grid_feedin_kw = max(field_values.get("peak_grid_feedin_kw", [0.0]))
        peak_grid_consumption_kw = max(field_values.get("peak_grid_consumption_kw", [0.0]))
        peak_battery_charging_kw = max(field_values.get("peak_battery_charging_kw", [0.0]))
        peak_battery_discharging_kw = max(field_values.get("peak_battery_discharging_kw", [0.0]))

        # Write to fronius_agg_weekly with timestamp = Monday 00:00:00 UTC
        timestamp = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)

        point = (
            Point(cfg.measurement_weekly)
            .tag("source", cfg.tag_source)
            .tag("site", cfg.tag_site)
            .field("grid_import_kwh", grid_import_kwh)
            .field("grid_export_kwh", grid_export_kwh)
            .field("peak_solar_kw", peak_solar_kw)
            .field("peak_consumption_kw", peak_consumption_kw)
            .field("peak_grid_feedin_kw", peak_grid_feedin_kw)
            .field("peak_grid_consumption_kw", peak_grid_consumption_kw)
            .field("peak_battery_charging_kw", peak_battery_charging_kw)
            .field("peak_battery_discharging_kw", peak_battery_discharging_kw)
            .time(timestamp, WritePrecision.S)
        )

        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=cfg.influx_bucket, org=cfg.influx_org, record=point)

        log_info(f"Wrote weekly rollup for week starting {monday} to {cfg.measurement_weekly} "
                 f"(import={grid_import_kwh:.2f} kWh, export={grid_export_kwh:.2f} kWh)")

    except Exception as e:
        log_error(f"Failed to compute weekly rollup for week starting {monday}: {e}")
        raise


def rollup_monthly(year: int, month: int, client: InfluxDBClient, cfg: Config) -> None:
    """Compute monthly rollup from daily aggregations for a given month.

    Queries fronius_agg_daily for all days in the given month,
    sums energy fields and takes the maximum of each peak field. Writes a single
    point to fronius_agg_monthly.

    Args:
        year: The year of the month to roll up.
        month: The month (1-12) to roll up.
        client: Connected InfluxDB client.
        cfg: Service configuration.
    """
    # Define the month range: 1st of month 00:00 UTC to 1st of next month 00:00 UTC
    start_utc = datetime(year, month, 1, tzinfo=timezone.utc)

    # Compute 1st of next month
    if month == 12:
        next_month_start = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month_start = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    stop_utc = next_month_start

    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    query_api = client.query_api()

    # Query all fields from fronius_agg_daily for this month
    query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_daily}")
  |> filter(fn: (r) => r["_field"] == "grid_import_kwh" or
                        r["_field"] == "grid_export_kwh" or
                        r["_field"] == "peak_solar_kw" or
                        r["_field"] == "peak_consumption_kw" or
                        r["_field"] == "peak_grid_feedin_kw" or
                        r["_field"] == "peak_grid_consumption_kw" or
                        r["_field"] == "peak_battery_charging_kw" or
                        r["_field"] == "peak_battery_discharging_kw")
'''

    try:
        tables = query_api.query(query, org=cfg.influx_org)

        # Collect values per field
        field_values: Dict[str, list] = {}
        for table in tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                if value is not None:
                    field_values.setdefault(field, []).append(float(value))

        # If no daily data exists for this month, skip
        if not field_values:
            log_debug(f"No daily data for {year}-{month:02d}, skipping monthly rollup.")
            return

        # Sum energy fields
        grid_import_kwh = sum(field_values.get("grid_import_kwh", [0.0]))
        grid_export_kwh = sum(field_values.get("grid_export_kwh", [0.0]))

        # Max of peak fields
        peak_solar_kw = max(field_values.get("peak_solar_kw", [0.0]))
        peak_consumption_kw = max(field_values.get("peak_consumption_kw", [0.0]))
        peak_grid_feedin_kw = max(field_values.get("peak_grid_feedin_kw", [0.0]))
        peak_grid_consumption_kw = max(field_values.get("peak_grid_consumption_kw", [0.0]))
        peak_battery_charging_kw = max(field_values.get("peak_battery_charging_kw", [0.0]))
        peak_battery_discharging_kw = max(field_values.get("peak_battery_discharging_kw", [0.0]))

        # Write to fronius_agg_monthly with timestamp = 1st of month 00:00:00 UTC
        timestamp = datetime(year, month, 1, tzinfo=timezone.utc)

        point = (
            Point(cfg.measurement_monthly)
            .tag("source", cfg.tag_source)
            .tag("site", cfg.tag_site)
            .field("grid_import_kwh", grid_import_kwh)
            .field("grid_export_kwh", grid_export_kwh)
            .field("peak_solar_kw", peak_solar_kw)
            .field("peak_consumption_kw", peak_consumption_kw)
            .field("peak_grid_feedin_kw", peak_grid_feedin_kw)
            .field("peak_grid_consumption_kw", peak_grid_consumption_kw)
            .field("peak_battery_charging_kw", peak_battery_charging_kw)
            .field("peak_battery_discharging_kw", peak_battery_discharging_kw)
            .time(timestamp, WritePrecision.S)
        )

        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=cfg.influx_bucket, org=cfg.influx_org, record=point)

        log_info(f"Wrote monthly rollup for {year}-{month:02d} to {cfg.measurement_monthly} "
                 f"(import={grid_import_kwh:.2f} kWh, export={grid_export_kwh:.2f} kWh)")

    except Exception as e:
        log_error(f"Failed to compute monthly rollup for {year}-{month:02d}: {e}")
        raise


def rollup_annual(year: int, client: InfluxDBClient, cfg: Config) -> None:
    """Compute annual rollup from monthly aggregations for a given year.

    Queries fronius_agg_monthly for all 12 months of the given year,
    sums energy fields and takes the maximum of each peak field. Writes a single
    point to fronius_agg_annual.

    Args:
        year: The year to roll up.
        client: Connected InfluxDB client.
        cfg: Service configuration.
    """
    # Define the year range: Jan 1st 00:00 UTC to Jan 1st next year 00:00 UTC
    start_utc = datetime(year, 1, 1, tzinfo=timezone.utc)
    stop_utc = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    query_api = client.query_api()

    # Query all fields from fronius_agg_monthly for this year
    query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_monthly}")
  |> filter(fn: (r) => r["_field"] == "grid_import_kwh" or
                        r["_field"] == "grid_export_kwh" or
                        r["_field"] == "peak_solar_kw" or
                        r["_field"] == "peak_consumption_kw" or
                        r["_field"] == "peak_grid_feedin_kw" or
                        r["_field"] == "peak_grid_consumption_kw" or
                        r["_field"] == "peak_battery_charging_kw" or
                        r["_field"] == "peak_battery_discharging_kw")
'''

    try:
        tables = query_api.query(query, org=cfg.influx_org)

        # Collect values per field
        field_values: Dict[str, list] = {}
        for table in tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                if value is not None:
                    field_values.setdefault(field, []).append(float(value))

        # If no monthly data exists for this year, skip
        if not field_values:
            log_debug(f"No monthly data for {year}, skipping annual rollup.")
            return

        # Sum energy fields
        grid_import_kwh = sum(field_values.get("grid_import_kwh", [0.0]))
        grid_export_kwh = sum(field_values.get("grid_export_kwh", [0.0]))

        # Max of peak fields
        peak_solar_kw = max(field_values.get("peak_solar_kw", [0.0]))
        peak_consumption_kw = max(field_values.get("peak_consumption_kw", [0.0]))
        peak_grid_feedin_kw = max(field_values.get("peak_grid_feedin_kw", [0.0]))
        peak_grid_consumption_kw = max(field_values.get("peak_grid_consumption_kw", [0.0]))
        peak_battery_charging_kw = max(field_values.get("peak_battery_charging_kw", [0.0]))
        peak_battery_discharging_kw = max(field_values.get("peak_battery_discharging_kw", [0.0]))

        # Write to fronius_agg_annual with timestamp = Jan 1st 00:00:00 UTC
        timestamp = datetime(year, 1, 1, tzinfo=timezone.utc)

        point = (
            Point(cfg.measurement_annual)
            .tag("source", cfg.tag_source)
            .tag("site", cfg.tag_site)
            .field("grid_import_kwh", grid_import_kwh)
            .field("grid_export_kwh", grid_export_kwh)
            .field("peak_solar_kw", peak_solar_kw)
            .field("peak_consumption_kw", peak_consumption_kw)
            .field("peak_grid_feedin_kw", peak_grid_feedin_kw)
            .field("peak_grid_consumption_kw", peak_grid_consumption_kw)
            .field("peak_battery_charging_kw", peak_battery_charging_kw)
            .field("peak_battery_discharging_kw", peak_battery_discharging_kw)
            .time(timestamp, WritePrecision.S)
        )

        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=cfg.influx_bucket, org=cfg.influx_org, record=point)

        log_info(f"Wrote annual rollup for {year} to {cfg.measurement_annual} "
                 f"(import={grid_import_kwh:.2f} kWh, export={grid_export_kwh:.2f} kWh)")

    except Exception as e:
        log_error(f"Failed to compute annual rollup for {year}: {e}")
        raise


def backfill_rollups(client: InfluxDBClient, cfg: Config) -> None:
    """Compute all missing weekly, monthly, and annual rollups from existing daily data.

    Determines the date range of existing daily aggregations, then iterates
    through all complete ISO weeks, months, and years within that range,
    calling the respective rollup functions. Since writes are idempotent
    (same timestamp overwrites), it is safe to recompute all rollups.

    Args:
        client: Connected InfluxDB client.
        cfg: Service configuration.
    """
    # Determine the date range of existing daily data
    query_api = client.query_api()

    # Query first daily aggregation date
    first_query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_daily}")
  |> first()
  |> keep(columns: ["_time"])
'''

    # Query last daily aggregation date
    last_query = f'''
from(bucket: "{cfg.influx_bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_daily}")
  |> last()
  |> keep(columns: ["_time"])
'''

    try:
        first_date = None
        tables = query_api.query(first_query, org=cfg.influx_org)
        for table in tables:
            for record in table.records:
                utc_time = record.get_time()
                first_date = utc_time.astimezone(LOCAL_TZ).date()
                break
            if first_date:
                break

        last_date = None
        tables = query_api.query(last_query, org=cfg.influx_org)
        for table in tables:
            for record in table.records:
                utc_time = record.get_time()
                last_date = utc_time.astimezone(LOCAL_TZ).date()
                break
            if last_date:
                break

        if first_date is None or last_date is None:
            log_info("No daily aggregation data found. Skipping rollup backfill.")
            return

        log_info(f"Backfilling rollups from daily data range: {first_date} to {last_date}")

        current_date = today_local()

        # --- Weekly rollups ---
        # Find the first Monday on or after the first daily date
        days_until_monday = (7 - first_date.weekday()) % 7
        first_monday = first_date + timedelta(days=days_until_monday)

        weekly_count = 0
        monday = first_monday
        while monday + timedelta(days=6) <= last_date and monday + timedelta(days=6) < current_date:
            try:
                rollup_weekly(monday, client, cfg)
                weekly_count += 1
            except Exception as e:
                log_error(f"Error computing weekly rollup for week starting {monday}: {e}")
            monday += timedelta(days=7)

        log_info(f"Weekly rollup backfill complete: {weekly_count} weeks computed.")

        # --- Monthly rollups ---
        monthly_count = 0
        year = first_date.year
        month = first_date.month

        while True:
            # A month is complete if the current date is past its end boundary
            _, last_day_of_month = calendar.monthrange(year, month)
            month_end = date(year, month, last_day_of_month)

            if month_end > last_date or month_end >= current_date:
                break

            try:
                rollup_monthly(year, month, client, cfg)
                monthly_count += 1
            except Exception as e:
                log_error(f"Error computing monthly rollup for {year}-{month:02d}: {e}")

            # Advance to next month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1

        log_info(f"Monthly rollup backfill complete: {monthly_count} months computed.")

        # --- Annual rollups ---
        annual_count = 0
        for y in range(first_date.year, last_date.year + 1):
            # A year is complete if the current date is past Dec 31 of that year
            year_end = date(y, 12, 31)
            if year_end >= current_date:
                break

            try:
                rollup_annual(y, client, cfg)
                annual_count += 1
            except Exception as e:
                log_error(f"Error computing annual rollup for {y}: {e}")

        log_info(f"Annual rollup backfill complete: {annual_count} years computed.")
        log_info(f"Rollup backfill summary: {weekly_count} weekly, {monthly_count} monthly, "
                 f"{annual_count} annual rollups computed.")

    except Exception as e:
        log_error(f"Failed to backfill rollups: {e}")
        raise


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def calculate_next_run_time(cfg: Config) -> datetime:
    """Calculate the next scheduled execution datetime in local time.

    Uses cfg.schedule_hour and cfg.schedule_minute (defaults: 0 and 15) to
    determine the target time. If today's scheduled time has already passed,
    returns tomorrow's scheduled time; otherwise returns today's.

    Args:
        cfg: Service configuration containing schedule_hour and schedule_minute.

    Returns:
        A timezone-aware datetime in the configured local timezone representing
        the next scheduled run.
    """
    now = datetime.now(LOCAL_TZ)
    scheduled_today = now.replace(
        hour=cfg.schedule_hour,
        minute=cfg.schedule_minute,
        second=0,
        microsecond=0,
    )

    if now >= scheduled_today:
        # Today's scheduled time has passed, return tomorrow's
        return scheduled_today + timedelta(days=1)
    else:
        return scheduled_today


def run_service(client: InfluxDBClient, cfg: Config) -> None:
    """Main scheduling loop for the aggregation service.

    On startup, performs a full backfill of daily aggregations and rollups.
    Then enters a loop that sleeps until the next scheduled time, aggregates
    yesterday's data, and triggers rollups when week/month/year boundaries
    are crossed.

    Args:
        client: Connected InfluxDB client.
        cfg: Service configuration.
    """
    # --- Startup: backfill ---
    try:
        backfill_daily(client, cfg)
    except Exception as e:
        log_error(f"Backfill daily failed on startup: {e}")

    try:
        backfill_rollups(client, cfg)
    except Exception as e:
        log_error(f"Backfill rollups failed on startup: {e}")

    log_info(f"Last successful aggregation: {datetime.now(LOCAL_TZ)}")

    # --- Main loop ---
    while not shutdown_requested():
        next_run_time = calculate_next_run_time(cfg)
        log_info(f"Next scheduled run: {next_run_time}")

        # Sleep until next_run_time, checking shutdown every 60 seconds
        while not shutdown_requested():
            now = datetime.now(LOCAL_TZ)
            if now >= next_run_time:
                break
            # Sleep in 60-second increments to allow graceful shutdown
            remaining = (next_run_time - now).total_seconds()
            time.sleep(min(60, remaining))

        if shutdown_requested():
            break

        # --- On wake: aggregate yesterday ---
        try:
            yesterday = date.today() - timedelta(days=1)

            # Aggregate yesterday's data
            result = aggregate_day(yesterday, client, cfg)
            if result is not None:
                write_daily_aggregation(yesterday, result, client, cfg)

            # Check if week boundary was crossed (yesterday was Sunday)
            if yesterday.weekday() == 6:
                # Yesterday was Sunday → roll up the week starting Monday
                monday = yesterday - timedelta(days=6)
                try:
                    rollup_weekly(monday, client, cfg)
                except Exception as e:
                    log_error(f"Weekly rollup failed for week starting {monday}: {e}")

            # Check if month boundary was crossed (yesterday was last day of month)
            last_day_of_month = calendar.monthrange(yesterday.year, yesterday.month)[1]
            if yesterday.day == last_day_of_month:
                try:
                    rollup_monthly(yesterday.year, yesterday.month, client, cfg)
                except Exception as e:
                    log_error(f"Monthly rollup failed for {yesterday.year}-{yesterday.month:02d}: {e}")

            # Check if year boundary was crossed (yesterday was Dec 31)
            if yesterday.month == 12 and yesterday.day == 31:
                try:
                    rollup_annual(yesterday.year, client, cfg)
                except Exception as e:
                    log_error(f"Annual rollup failed for {yesterday.year}: {e}")

            log_info(f"Last successful aggregation: {datetime.now(LOCAL_TZ)}")

        except Exception as e:
            log_error(f"Error during scheduled aggregation: {e}")
            # Don't crash the service — log and continue to next iteration

    log_info("Shutdown requested. Exiting scheduling loop.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fronius Aggregation Service")
    parser.add_argument("--backfill-only", action="store_true",
                        help="Run backfill and exit without entering schedule loop")
    args = parser.parse_args()

    cfg = Config()
    cfg.validate()

    log_info("=" * 60)
    log_info("Fronius Aggregation Service starting")
    log_info(f"  InfluxDB: {cfg.influx_url}")
    log_info(f"  Bucket: {cfg.influx_bucket}")
    log_info(f"  Timezone: {os.getenv('TZ', 'UTC')}")
    log_info(f"  Schedule: {cfg.schedule_hour:02d}:{cfg.schedule_minute:02d} local")
    log_info(f"  Measurements: {cfg.measurement_daily}, {cfg.measurement_weekly}, "
             f"{cfg.measurement_monthly}, {cfg.measurement_annual}")
    log_info("=" * 60)

    client = connect_influxdb(cfg)

    try:
        if args.backfill_only:
            # Run backfill only and exit
            backfill_daily(client, cfg)
            backfill_rollups(client, cfg)
            log_info("Backfill complete. Exiting (--backfill-only mode).")
            return

        # Run the full service (backfill + scheduling loop)
        run_service(client, cfg)
    finally:
        client.close()
        log_info("InfluxDB connection closed. Goodbye.")


if __name__ == "__main__":
    main()
