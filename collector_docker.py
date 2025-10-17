#!/usr/bin/env python3
"""
Fronius GEN24 Lean Collector (Env-driven config + Colorized + Logging + Totals + Unit Tags)

All configuration values are read from environment variables (with sensible defaults).

Environment variables used (examples):
  FRONIUS_INVERTER_HOST=fronius
  FRONIUS_INVERTER_USE_HTTPS=false
  FRONIUS_INVERTER_VERIFY_SSL=false
  FRONIUS_INVERTER_DEVICE_ID=1

  INFLUX_URL=http://INFLUXDB_HOST:INFLUXDB_PORT
  INFLUX_TOKEN=INFLUXDB_TOKEN
  INFLUX_ORG=INFLUXDB_ORG
  INFLUX_BUCKET=INFLUXDB_BUCKET

  POLLING_INTERVAL=10

  TAG_SOURCE=SymoGEN24
  TAG_SITE=home

The script still supports -v/--verbose and --no-color CLI flags.
"""

import argparse
import copy
import os
import sys
import time
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# at top of file, after imports
from dotenv import load_dotenv

# Try to load .env from the working directory (doesn't overwrite already-set env vars)
# If you want the .env to *override* existing environment variables, pass override=True.
load_dotenv()  # loads .env into os.environ (only fills variables that are not already set)

# -----------------------
# Globals / Verbose
# -----------------------
VERBOSE = False
USE_COLOR = True
CFG: Dict[str, Any] = {}
LOG_FILE = "/app/logs/collector.log"

# -----------------------
# Logging helpers
# -----------------------
def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d/%H:%M:%S")

def log_to_file(line: str):
    """Append plain text log line to collector.log"""
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def print_log(line: str, color: Optional[str] = None):
    """Unified print + logfile output"""
    line_plain = remove_ansi(line)
    log_to_file(line_plain)
    if color and USE_COLOR:
        print(colorize(line, color))
    else:
        print(line)

def colorize(text: str, color: str) -> str:
    if not USE_COLOR:
        return text
    colors = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "gray": "\033[90m",
        "reset": "\033[0m",
    }
    return f"{colors.get(color,'')}{text}{colors['reset']}"

def remove_ansi(text: str) -> str:
    import re
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)

def info(msg): print_log(f"{ts()} [INFO] {msg}", "cyan")
def warn(msg): print_log(f"{ts()} [WARN] {msg}", "yellow")
def err(msg):  print_log(f"{ts()} [ERROR] {msg}", "red")

def vprint(msg: str):
    if VERBOSE:
        print_log(f"{ts()} [VERBOSE] {msg}", "gray")

# -----------------------
# Helpers
# -----------------------
def safe_val(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default

def round2(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except Exception:
        return None

def kW_from_W(w: Optional[float]) -> Optional[float]:
    if w is None:
        return None
    return round2(safe_val(w) / 1000.0)

def kWh_from_Wh(w: Optional[float]) -> Optional[float]:
    if w is None:
        return None
    return round2(safe_val(w) / 1000.0)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

# -----------------------
# Default Config
# -----------------------
DEFAULT_CONFIG = {
    "inverter": {
        "host": "fronius",
        "use_https": False,
        "verify_ssl": False,
        "device_id": 1
    },
    "influxdb": {
        "url": "http://YOUR_INFLUXDB_HOST:8086",
        "token": "YOUR_INFLUXDB_TOKEN",
        "org": "org",
        "bucket": "fronius_clean"
    },
    "polling": {
        "interval": 10
    },
    "tags": {
        "source": "SymoGEN24",
        "site": "home"
    }
}

# -----------------------
# Environment config loader
# -----------------------

def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default

def env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def load_config_from_env() -> Dict[str, Any]:
    """Build configuration from environment variables with sensible defaults."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    # Inverter
    cfg['inverter']['host'] = os.environ.get('FRONIUS_INVERTER_HOST', cfg['inverter']['host'])
    cfg['inverter']['use_https'] = env_bool('FRONIUS_INVERTER_USE_HTTPS', cfg['inverter']['use_https'])
    cfg['inverter']['verify_ssl'] = env_bool('FRONIUS_INVERTER_VERIFY_SSL', cfg['inverter']['verify_ssl'])
    cfg['inverter']['device_id'] = env_int('FRONIUS_INVERTER_DEVICE_ID', cfg['inverter']['device_id'])

    # InfluxDB
    cfg['influxdb']['url'] = os.environ.get('INFLUX_URL', cfg['influxdb']['url'])
    cfg['influxdb']['token'] = os.environ.get('INFLUX_TOKEN', cfg['influxdb']['token'])
    cfg['influxdb']['org'] = os.environ.get('INFLUX_ORG', cfg['influxdb']['org'])
    cfg['influxdb']['bucket'] = os.environ.get('INFLUX_BUCKET', cfg['influxdb']['bucket'])

    # Polling
    cfg['polling']['interval'] = env_float('POLLING_INTERVAL', cfg['polling']['interval'])

    # Tags
    cfg['tags']['source'] = os.environ.get('TAG_SOURCE', cfg['tags']['source'])
    cfg['tags']['site'] = os.environ.get('TAG_SITE', cfg['tags']['site'])

    # Optionally allow passing tags via TAGS env var as "k1=v1,k2=v2"
    tags_override = os.environ.get('TAGS')
    if tags_override:
        for pair in tags_override.split(','):
            if '=' in pair:
                k, v = pair.split('=', 1)
                cfg['tags'][k.strip()] = v.strip()

    info("Config loaded from environment variables")
    vprint(f"Config: {cfg}")
    return cfg

# -----------------------
# HTTP
# -----------------------

def base_url() -> str:
    proto = "https" if CFG["inverter"]["use_https"] else "http"
    return f"{proto}://{CFG['inverter']['host']}"

def fetch_json(path: str, retries: int = 3, backoff: float = 2.0) -> Optional[dict]:
    url = base_url() + path
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=10, verify=CFG["inverter"]["verify_ssl"])
            r.raise_for_status()
            return r.json()
        except Exception as e:
            warn(f"{url} failed (attempt {attempt}/{retries}): {e}")
            time.sleep(delay)
            delay *= backoff
    return None

# -----------------------
# Influx writing
# -----------------------

def write_influx(write_api, fields: Dict[str, Any]):
    if not fields:
        return
    p = Point("fronius_clean").time(now_utc(), WritePrecision.S)

    for tk, tv in CFG.get("tags", {}).items():
        p.tag(tk, str(tv))

    for fk, (fv, unit) in fields.items():
        if fv is not None:
            p.field(fk, fv)
            p.tag(f"{fk}_unit", unit)

    try:
        write_api.write(bucket=CFG["influxdb"]["bucket"], org=CFG["influxdb"]["org"], record=p)
    except Exception as e:
        err(f"Influx write failed: {e}")

# -----------------------
# Pretty output
# -----------------------

def vprint_summary(fields: Dict[str, Any]):
    """Pretty, single-line verbose summary in original layout with color-coded grid/battery"""
    if not VERBOSE:
        return

    def val(key):
        v = fields.get(key, (None,))[0]
        return "—" if v is None else f"{v:.2f}"

    solar = fields.get("Solar_Produced_Current", (0,))[0]
    load = fields.get("Consumption_Current", (0,))[0]
    soc = fields.get("Battery_SOC", (0,))[0]
    grid_cons = fields.get("Grid_Consumption_Current", (0,))[0]
    grid_feed = fields.get("Grid_FeedIn_Current", (0,))[0]
    batt_c = fields.get("Battery_Charging", (0,))[0]
    batt_d = fields.get("Battery_Discharging", (0,))[0]

    # Grid coloring
    grid_plus = colorize(f"+{grid_feed:.2f}", "green") if grid_feed and grid_feed > 0 else colorize("+0.00", "reset")
    grid_minus = colorize(f"-{grid_cons:.2f}", "red") if grid_cons and grid_cons > 0 else colorize("-0.00", "reset")

    # Battery coloring
    batt_plus = colorize(f"+{batt_c:.2f}", "green") if batt_c and batt_c > 0 else colorize("+0.00", "reset")
    batt_minus = colorize(f"-{batt_d:.2f}", "red") if batt_d and batt_d > 0 else colorize("-0.00", "reset")

    line = (
        f"[{ts()}] "
        f"Solar={colorize(val('Solar_Produced_Current'),'yellow')} | "
        f"Load={colorize(val('Consumption_Current'),'magenta')} | "
        f"Grid{grid_plus}/{grid_minus}kW | "
        f"SOC={colorize(val('Battery_SOC'),'cyan')}% | "
        f"Batt{batt_plus}/{batt_minus}kW | "
        f"Auto={colorize(val('Autonomy_Percentage'),'blue')}% | "
        f"ConsTot={colorize(val('Consumption_Total'),'yellow')}kWh | "
        f"GridConsTot={colorize(val('Grid_Consumption_Total'),'red')}kWh | "
        f"GridFeedTot={colorize(val('Grid_FeedIn_Total'),'green')}kWh"
    )

    print_log(line)

# -----------------------
# Main loop
# -----------------------

def main():
    influx = InfluxDBClient(url=CFG["influxdb"]["url"], token=CFG["influxdb"]["token"], org=CFG["influxdb"]["org"])
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    while True:
        loop_start = time.time()
        fields: Dict[str, Any] = {}

        inv_common = fetch_json(
            f"/solar_api/v1/GetInverterRealtimeData.cgi?Scope=Device&DeviceId={CFG['inverter']['device_id']}&DataCollection=CommonInverterData"
        )
        powerflow  = fetch_json("/solar_api/v1/GetPowerFlowRealtimeData.fcgi")
        meter_data = fetch_json("/solar_api/v1/GetMeterRealtimeData.cgi?Scope=Device&DeviceId=0")

        site_pf = powerflow.get("Body", {}).get("Data", {}).get("Site", {}) if powerflow else {}

        # --- Solar produced current ---
        pv_kw = kW_from_W(site_pf.get("P_PV")) if site_pf else None
        fields["Solar_Produced_Current"] = (pv_kw, "kW")

        # --- Consumption current ---
        cons_kw = None
        if site_pf and site_pf.get("P_Load") is not None:
            cons_kw = round2(abs(float(site_pf["P_Load"])) / 1000.0)
        fields["Consumption_Current"] = (cons_kw, "kW")

        # --- Grid import/export ---
        gridP = site_pf.get("P_Grid")
        if gridP is not None:
            if gridP >= 0:
                fields["Grid_Consumption_Current"] = (kW_from_W(gridP), "kW")
                fields["Grid_FeedIn_Current"] = (0.0, "kW")
            else:
                fields["Grid_FeedIn_Current"] = (kW_from_W(-gridP), "kW")
                fields["Grid_Consumption_Current"] = (0.0, "kW")

        # --- Totals from meter ---
        if meter_data:
            data = meter_data.get("Body", {}).get("Data", {})
            fields["Grid_FeedIn_Total"] = (kWh_from_Wh(data.get("EnergyReal_WAC_Sum_Produced")), "kWh")
            fields["Grid_Consumption_Total"] = (kWh_from_Wh(data.get("EnergyReal_WAC_Sum_Consumed")), "kWh")
            fields["Consumption_Total"] = (kWh_from_Wh(data.get("EnergyReal_WAC_Minus_Absolute")), "kWh")
            fields["Solar_Produced_Total"] = (kWh_from_Wh(data.get("EnergyReal_WAC_Plus_Absolute")), "kWh")

        # --- Battery SOC ---
        if powerflow:
            invs = powerflow.get("Body", {}).get("Data", {}).get("Inverters", {})
            for _, inv in invs.items():
                if "SOC" in inv:
                    fields["Battery_SOC"] = (round2(inv["SOC"]), "%")
                    break

        # --- Battery Charging/Discharging ---
        if "P_Akku" in site_pf:
            p_akku = site_pf["P_Akku"]
            if p_akku < 0:
                fields["Battery_Charging"] = (kW_from_W(-p_akku), "kW")
                fields["Battery_Discharging"] = (0.0, "kW")
            elif p_akku > 0:
                fields["Battery_Discharging"] = (kW_from_W(p_akku), "kW")
                fields["Battery_Charging"] = (0.0, "kW")
            else:
                fields["Battery_Charging"] = (0.0, "kW")
                fields["Battery_Discharging"] = (0.0, "kW")

        # --- Autonomy percentage ---
        auto = site_pf.get("rel_Autonomy")
        if auto is not None:
            fields["Autonomy_Percentage"] = (round2(auto), "%")

        # --- Logged_At ---
        fields["Logged_At"] = (int(time.time()), "s")

        # --- Write ---
        write_influx(write_api, fields)
        vprint_summary(fields)

        loop_dur = time.time() - loop_start
        wait = max(0.0, CFG["polling"]["interval"] - loop_dur)
        time.sleep(wait)

# -----------------------
# Entry
# -----------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-color", action="store_true", help="disable colorized terminal output")
    args = parser.parse_args()
    VERBOSE = args.verbose
    USE_COLOR = not args.no_color
    CFG = load_config_from_env()
    info("🚀 Fronius Collector started (env-driven config)")
    main()