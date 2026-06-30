#!/usr/bin/env python3
"""
Fronius Aggregation Tasks

Contains the core aggregation logic: daily computation, backfill, rollups, and scheduling.
Imported by aggregator.py.
"""

import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Dict, Any

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from aggregator import (
    Config, LOCAL_TZ, log_info, log_warn, log_error, log_debug,
    local_day_to_utc_range, today_local, yesterday_local,
    shutdown_requested, connect_influxdb,
    MAX_RETRY_DELAY, INITIAL_RETRY_DELAY,
)


# ---------------------------------------------------------------------------
# Daily Aggregation
# ---------------------------------------------------------------------------

# Fields to compute spread (max - min) for energy totals
CUMULATIVE_FIELDS = ["Grid_Consumption_Total", "Grid_FeedIn_Total"]

# Fields to compute max() for peak power values
PEAK_FIELDS = [
    "Solar_Produced_Current",
    "Consumption_Current",
    "Grid_FeedIn_Current",
    "Grid_Consumption_Current",
    "Battery_Charging",
    "Battery_Discharging",
]

# Fields to compute mean() for average values
MEAN_FIELDS = ["Autonomy_Percentage"]

MEAN_TO_AGG = {
    "Autonomy_Percentage": "avg_autonomy_pct",
}

# Mapping from raw field names to aggregated field names
CUMULATIVE_TO_AGG = {
    "Grid_Consumption_Total": "grid_import_kwh",
    "Grid_FeedIn_Total": "grid_export_kwh",
}

PEAK_TO_AGG = {
    "Solar_Produced_Current": "peak_solar_kw",
    "Consumption_Current": "peak_consumption_kw",
    "Grid_FeedIn_Current": "peak_grid_feedin_kw",
    "Grid_Consumption_Current": "peak_grid_consumption_kw",
    "Battery_Charging": "peak_battery_charging_kw",
    "Battery_Discharging": "peak_battery_discharging_kw",
}


def aggregate_day(client: InfluxDBClient, cfg: Config, d: date) -> Optional[Dict[str, float]]:
    """Compute daily energy totals and peak power values for a given calendar day.

    Uses Flux spread() for cumulative counters and max() for instantaneous power.
    Returns None if no raw data exists for that day.
    Returns a dict with keys matching the aggregated measurement schema.
    """
    query_api = client.query_api()
    start_utc, stop_utc = local_day_to_utc_range(d)

    # Format timestamps for Flux
    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    result = {}

    # --- Energy totals via spread() ---
    fields_filter = " or ".join(
        f'r["_field"] == "{f}"' for f in CUMULATIVE_FIELDS
    )
    query_spread = f'''from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => {fields_filter})
  |> spread()'''

    try:
        tables = query_api.query(query_spread)
    except Exception as e:
        log_error(f"Flux spread query failed for {d}: {e}")
        return None

    has_data = False
    for table in tables:
        for record in table.records:
            field = record.values.get("_field")
            value = record.get_value()
            if field in CUMULATIVE_TO_AGG and value is not None:
                result[CUMULATIVE_TO_AGG[field]] = max(0.0, float(value))
                has_data = True

    if not has_data:
        log_debug(f"No raw data for {d}, skipping.")
        return None

    # --- Peak power values via max() ---
    peak_filter = " or ".join(
        f'r["_field"] == "{f}"' for f in PEAK_FIELDS
    )
    query_max = f'''from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => {peak_filter})
  |> max()'''

    try:
        tables = query_api.query(query_max)
    except Exception as e:
        log_warn(f"Flux max query failed for {d}: {e}")
        # Continue with energy data even if peaks fail
        for agg_field in PEAK_TO_AGG.values():
            result.setdefault(agg_field, 0.0)
        return result

    for table in tables:
        for record in table.records:
            field = record.values.get("_field")
            value = record.get_value()
            if field in PEAK_TO_AGG and value is not None:
                result[PEAK_TO_AGG[field]] = max(0.0, float(value))

    # Ensure all peak fields have a value (default 0.0 if not present in raw data)
    for agg_field in PEAK_TO_AGG.values():
        result.setdefault(agg_field, 0.0)

    # --- Average values via mean() ---
    mean_filter = " or ".join(
        f'r["_field"] == "{f}"' for f in MEAN_FIELDS
    )
    query_mean = f'''from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => {mean_filter})
  |> mean()'''

    try:
        tables = query_api.query(query_mean)
        for table in tables:
            for record in table.records:
                field = record.values.get("_field")
                value = record.get_value()
                if field in MEAN_TO_AGG and value is not None:
                    result[MEAN_TO_AGG[field]] = round(float(value), 2)
    except Exception as e:
        log_warn(f"Flux mean query failed for {d}: {e}")

    # Ensure all mean fields have a value
    for agg_field in MEAN_TO_AGG.values():
        result.setdefault(agg_field, 0.0)

    # Ensure energy fields have a value
    for agg_field in CUMULATIVE_TO_AGG.values():
        result.setdefault(agg_field, 0.0)

    return result


def write_daily_aggregation(client: InfluxDBClient, cfg: Config, d: date, data: Dict[str, float]):
    """Write a single daily aggregation point to fronius_agg_daily.

    Timestamp: start of day 00:00:00 UTC.
    Tags: source, site.
    Fields: grid_import_kwh, grid_export_kwh, peak_*.
    """
    write_api = client.write_api(write_options=SYNCHRONOUS)

    # Timestamp = start of the local day expressed in UTC
    start_utc, _ = local_day_to_utc_range(d)

    point = Point(cfg.measurement_daily) \
        .time(start_utc, WritePrecision.S) \
        .tag("source", cfg.tag_source) \
        .tag("site", cfg.tag_site)

    for field_name, value in data.items():
        point.field(field_name, round(float(value), 4))

    try:
        write_api.write(bucket=cfg.influx_bucket, org=cfg.influx_org, record=point)
        log_debug(f"Wrote daily aggregation for {d}: import={data.get('grid_import_kwh', 0):.2f} kWh, "
                  f"export={data.get('grid_export_kwh', 0):.2f} kWh")
    except Exception as e:
        log_error(f"Failed to write daily aggregation for {d}: {e}")
        raise


# ---------------------------------------------------------------------------
# Weekly, Monthly, and Annual Rollups
# ---------------------------------------------------------------------------

# All aggregated fields (energy + peaks)
ENERGY_FIELDS = ["grid_import_kwh", "grid_export_kwh"]
ALL_PEAK_FIELDS = list(PEAK_TO_AGG.values())
ALL_MEAN_FIELDS = list(MEAN_TO_AGG.values())


def _query_daily_range(client: InfluxDBClient, cfg: Config, start_utc: datetime, stop_utc: datetime) -> list:
    """Query fronius_agg_daily for all points in a UTC time range.

    Returns list of dicts, one per day, with all field values.
    """
    query_api = client.query_api()
    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_fields = ENERGY_FIELDS + ALL_PEAK_FIELDS + ALL_MEAN_FIELDS
    fields_filter = " or ".join(f'r["_field"] == "{f}"' for f in all_fields)

    query = f'''from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_daily}")
  |> filter(fn: (r) => {fields_filter})
  |> sort(columns: ["_time"])'''

    try:
        tables = query_api.query(query)
    except Exception as e:
        log_error(f"Failed to query daily range {start_str} to {stop_str}: {e}")
        return []

    # Group by timestamp
    by_time: Dict[str, Dict[str, float]] = {}
    for table in tables:
        for record in table.records:
            ts_key = record.get_time().isoformat()
            field = record.values.get("_field")
            value = record.get_value()
            if ts_key not in by_time:
                by_time[ts_key] = {}
            if field and value is not None:
                by_time[ts_key][field] = float(value)

    return list(by_time.values())


def _sum_and_max(daily_records: list) -> Optional[Dict[str, float]]:
    """Compute rollup from a list of daily records: sum energy, max peaks, avg means.

    Returns None if no records.
    """
    if not daily_records:
        return None

    result = {}
    # Sum energy fields
    for f in ENERGY_FIELDS:
        result[f] = sum(rec.get(f, 0.0) for rec in daily_records)

    # Max peak fields
    for f in ALL_PEAK_FIELDS:
        result[f] = max((rec.get(f, 0.0) for rec in daily_records), default=0.0)

    # Average mean fields (average of daily averages)
    ALL_MEAN_FIELDS = list(MEAN_TO_AGG.values())
    for f in ALL_MEAN_FIELDS:
        values = [rec.get(f, 0.0) for rec in daily_records if rec.get(f) is not None]
        result[f] = round(sum(values) / len(values), 2) if values else 0.0

    return result


def _write_rollup_point(client: InfluxDBClient, cfg: Config, measurement: str,
                        ts: datetime, data: Dict[str, float]):
    """Write a single rollup point to the specified measurement."""
    write_api = client.write_api(write_options=SYNCHRONOUS)

    point = Point(measurement) \
        .time(ts, WritePrecision.S) \
        .tag("source", cfg.tag_source) \
        .tag("site", cfg.tag_site)

    for field_name, value in data.items():
        point.field(field_name, round(float(value), 4))

    try:
        write_api.write(bucket=cfg.influx_bucket, org=cfg.influx_org, record=point)
    except Exception as e:
        log_error(f"Failed to write rollup to {measurement}: {e}")
        raise


def rollup_weekly(client: InfluxDBClient, cfg: Config, monday: date):
    """Compute weekly rollup from daily data for the ISO week starting on `monday`.

    Sums energy fields, takes max of peak fields from fronius_agg_daily.
    Writes to fronius_agg_weekly with timestamp = Monday 00:00:00 UTC.
    """
    # Week range: Monday 00:00 UTC to next Monday 00:00 UTC
    start_utc = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=timezone.utc)
    stop_utc = start_utc + timedelta(days=7)

    daily_records = _query_daily_range(client, cfg, start_utc, stop_utc)
    data = _sum_and_max(daily_records)

    if data is None:
        log_debug(f"No daily data for week starting {monday}, skipping weekly rollup.")
        return

    _write_rollup_point(client, cfg, cfg.measurement_weekly, start_utc, data)
    log_debug(f"Weekly rollup for {monday}: import={data['grid_import_kwh']:.2f} kWh, "
              f"export={data['grid_export_kwh']:.2f} kWh")


def rollup_monthly(client: InfluxDBClient, cfg: Config, year: int, month: int):
    """Compute monthly rollup from daily data for the given year/month.

    Sums energy fields, takes max of peak fields from fronius_agg_daily.
    Writes to fronius_agg_monthly with timestamp = 1st of month 00:00:00 UTC.
    """
    start_utc = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    # Next month
    if month == 12:
        stop_utc = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        stop_utc = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    daily_records = _query_daily_range(client, cfg, start_utc, stop_utc)
    data = _sum_and_max(daily_records)

    if data is None:
        log_debug(f"No daily data for {year}-{month:02d}, skipping monthly rollup.")
        return

    _write_rollup_point(client, cfg, cfg.measurement_monthly, start_utc, data)
    log_debug(f"Monthly rollup for {year}-{month:02d}: import={data['grid_import_kwh']:.2f} kWh, "
              f"export={data['grid_export_kwh']:.2f} kWh")


def rollup_annual(client: InfluxDBClient, cfg: Config, year: int):
    """Compute annual rollup from monthly data for the given year.

    Sums energy fields, takes max of peak fields from fronius_agg_monthly.
    Writes to fronius_agg_annual with timestamp = Jan 1st 00:00:00 UTC.
    """
    query_api = client.query_api()
    start_utc = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    stop_utc = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_fields = ENERGY_FIELDS + ALL_PEAK_FIELDS
    fields_filter = " or ".join(f'r["_field"] == "{f}"' for f in all_fields)

    query = f'''from(bucket: "{cfg.influx_bucket}")
  |> range(start: {start_str}, stop: {stop_str})
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_monthly}")
  |> filter(fn: (r) => {fields_filter})'''

    try:
        tables = query_api.query(query)
    except Exception as e:
        log_error(f"Failed to query monthly data for annual rollup {year}: {e}")
        return

    monthly_records = []
    by_time: Dict[str, Dict[str, float]] = {}
    for table in tables:
        for record in table.records:
            ts_key = record.get_time().isoformat()
            field = record.values.get("_field")
            value = record.get_value()
            if ts_key not in by_time:
                by_time[ts_key] = {}
            if field and value is not None:
                by_time[ts_key][field] = float(value)
    monthly_records = list(by_time.values())

    data = _sum_and_max(monthly_records)
    if data is None:
        log_debug(f"No monthly data for {year}, skipping annual rollup.")
        return

    _write_rollup_point(client, cfg, cfg.measurement_annual, start_utc, data)
    log_info(f"Annual rollup for {year}: import={data['grid_import_kwh']:.2f} kWh, "
             f"export={data['grid_export_kwh']:.2f} kWh")


def backfill_rollups(client: InfluxDBClient, cfg: Config):
    """Compute all missing weekly, monthly, and annual rollups from existing daily data."""
    first_raw = get_first_raw_data_date(client, cfg)
    if first_raw is None:
        return

    yesterday = yesterday_local()

    # --- Weekly rollups ---
    # Find the first Monday on or after first_raw
    days_since_monday = first_raw.weekday()  # 0=Monday
    first_monday = first_raw - timedelta(days=days_since_monday)
    if first_monday < first_raw:
        first_monday += timedelta(days=7)

    # Iterate through complete weeks up to yesterday
    current_monday = first_monday
    weeks_processed = 0
    while current_monday + timedelta(days=6) <= yesterday:
        rollup_weekly(client, cfg, current_monday)
        current_monday += timedelta(days=7)
        weeks_processed += 1

    if weeks_processed > 0:
        log_info(f"Weekly rollup backfill: {weeks_processed} weeks processed.")

    # --- Monthly rollups ---
    current_year = first_raw.year
    current_month = first_raw.month
    months_processed = 0

    while True:
        # Only rollup complete months (month must be before current month)
        if current_year > yesterday.year:
            break
        if current_year == yesterday.year and current_month >= yesterday.month:
            break

        rollup_monthly(client, cfg, current_year, current_month)
        months_processed += 1

        current_month += 1
        if current_month > 12:
            current_month = 1
            current_year += 1

    if months_processed > 0:
        log_info(f"Monthly rollup backfill: {months_processed} months processed.")

    # --- Annual rollups ---
    years_processed = 0
    for year in range(first_raw.year, yesterday.year):
        # Only rollup complete years
        rollup_annual(client, cfg, year)
        years_processed += 1

    if years_processed > 0:
        log_info(f"Annual rollup backfill: {years_processed} years processed.")


# ---------------------------------------------------------------------------
# Backfill and Schedule
# ---------------------------------------------------------------------------

def get_first_raw_data_date(client: InfluxDBClient, cfg: Config) -> Optional[date]:
    """Query fronius_clean for the earliest timestamp, return as local date."""
    query_api = client.query_api()
    query = f'''from(bucket: "{cfg.influx_bucket}")
  |> range(start: -10y)
  |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
  |> filter(fn: (r) => r["_field"] == "Grid_Consumption_Total")
  |> first()
  |> keep(columns: ["_time"])'''

    try:
        tables = query_api.query(query)
        for table in tables:
            for record in table.records:
                ts = record.values.get("_time")
                if ts:
                    local_dt = ts.astimezone(LOCAL_TZ)
                    return local_dt.date()
    except Exception as e:
        log_error(f"Failed to query first raw data date: {e}")
    return None


def get_last_aggregated_date(client: InfluxDBClient, cfg: Config) -> Optional[date]:
    """Query fronius_agg_daily for the latest timestamp, return as local date."""
    query_api = client.query_api()
    query = f'''from(bucket: "{cfg.influx_bucket}")
  |> range(start: -10y)
  |> filter(fn: (r) => r["_measurement"] == "{cfg.measurement_daily}")
  |> filter(fn: (r) => r["_field"] == "grid_import_kwh")
  |> last()
  |> keep(columns: ["_time"])'''

    try:
        tables = query_api.query(query)
        for table in tables:
            for record in table.records:
                ts = record.values.get("_time")
                if ts:
                    local_dt = ts.astimezone(LOCAL_TZ)
                    return local_dt.date()
    except Exception as e:
        log_debug(f"No existing aggregated data found: {e}")
    return None


def run_backfill(client: InfluxDBClient, cfg: Config):
    """Backfill all missing daily aggregations from first raw data to yesterday.
    
    Placeholder — full implementation in Task 3.
    """
    first_raw = get_first_raw_data_date(client, cfg)
    if first_raw is None:
        log_info("No raw data found. Nothing to backfill.")
        return

    last_agg = get_last_aggregated_date(client, cfg)
    start_date = (last_agg + timedelta(days=1)) if last_agg else first_raw
    end_date = yesterday_local()

    if start_date > end_date:
        log_info("Daily aggregation is up to date. No backfill needed.")
        return

    total_days = (end_date - start_date).days + 1
    log_info(f"Backfilling {total_days} days: {start_date} → {end_date}")

    processed = 0
    current = start_date
    while current <= end_date:
        if shutdown_requested():
            log_info("Shutdown requested during backfill. Stopping.")
            break

        data = aggregate_day(client, cfg, current)
        if data is not None:
            write_daily_aggregation(client, cfg, current, data)

        processed += 1
        if processed % 30 == 0:
            log_info(f"  Backfill progress: {processed}/{total_days} days ({current})")

        current += timedelta(days=1)

    log_info(f"Daily backfill complete. Processed {processed} days.")

    # Compute missing weekly/monthly/annual rollups from daily data
    backfill_rollups(client, cfg)


def run_schedule_loop(client: InfluxDBClient, cfg: Config):
    """Enter the daily schedule loop. Aggregates yesterday at the configured time.
    
    Placeholder — full implementation in Task 5.
    """
    log_info(f"Entering schedule loop. Next run at {cfg.schedule_hour:02d}:{cfg.schedule_minute:02d} local.")

    while not shutdown_requested():
        now = datetime.now(LOCAL_TZ)
        # Calculate next run time
        next_run = now.replace(hour=cfg.schedule_hour, minute=cfg.schedule_minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        wait_seconds = (next_run - now).total_seconds()
        log_info(f"Next aggregation at {next_run.strftime('%Y-%m-%d %H:%M')} local "
                 f"(in {wait_seconds/3600:.1f}h)")

        # Sleep in small increments to check for shutdown
        while not shutdown_requested() and datetime.now(LOCAL_TZ) < next_run:
            time.sleep(min(60, (next_run - datetime.now(LOCAL_TZ)).total_seconds()))

        if shutdown_requested():
            break

        # Aggregate yesterday
        yday = yesterday_local()
        log_info(f"Running daily aggregation for {yday}")
        data = aggregate_day(client, cfg, yday)
        if data is not None:
            write_daily_aggregation(client, cfg, yday, data)
            log_info(f"Daily aggregation complete for {yday}: "
                     f"import={data.get('grid_import_kwh', 0):.2f} kWh, "
                     f"export={data.get('grid_export_kwh', 0):.2f} kWh")
        else:
            log_warn(f"No data for {yday}, skipping.")

        # Rollups: check if a week/month/year boundary was crossed
        today = today_local()

        # Weekly: if today is Monday, yesterday completed a week
        if today.weekday() == 0:  # Monday
            last_monday = yday - timedelta(days=6)
            log_info(f"Week boundary crossed. Rolling up week starting {last_monday}")
            rollup_weekly(client, cfg, last_monday)

        # Monthly: if today is the 1st, yesterday completed a month
        if today.day == 1:
            log_info(f"Month boundary crossed. Rolling up {yday.year}-{yday.month:02d}")
            rollup_monthly(client, cfg, yday.year, yday.month)

        # Annual: if today is Jan 1st, yesterday completed a year
        if today.month == 1 and today.day == 1:
            log_info(f"Year boundary crossed. Rolling up {yday.year}")
            rollup_annual(client, cfg, yday.year)

        log_info(f"Last successful aggregation: {datetime.now(LOCAL_TZ).isoformat()}")
