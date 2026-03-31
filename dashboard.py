# ─────────────────────────────────────────────────────────────────────────
#   dashboard.py – Fronius Energy Monitor (dark‑theme, live InfluxDB)
# ─────────────────────────────────────────────────────────────────────────
import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

# ---------------------------------------------------------------------------
# Load environment variables (same as the collector)
# ---------------------------------------------------------------------------
load_dotenv()  # .env → os.environ
INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "fronius_clean")

if not INFLUX_TOKEN:
    st.error("❌ INFLUX_TOKEN is not set. Check your .env file.")
    st.stop()

# ---------------------------------------------------------------------------
# InfluxDB client
# ---------------------------------------------------------------------------
client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
query_api = client.query_api()


# ---------------------------------------------------------------------------
# Helper – fetch the *latest* value for a field
# ---------------------------------------------------------------------------
def fetch_latest(field: str) -> float | None:
    """
    Return the most recent value for the given field from the
    `fronius_clean` measurement.  Returns None if the field is missing.
    """
    flux = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -10m)
      |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
      |> filter(fn: (r) => r["_field"] == "{field}")
      |> last()
    '''
    try:
        result = query_api.query(flux)
        for table in result:
            for record in table.records:
                return record.get_value()
    except Exception as e:
        st.warning(f"⚠️ Influx query error for {field}: {e}")
    return None


# ---------------------------------------------------------------------------
# Streamlit layout & theme
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Fronius Energy Monitor",
    page_icon="🔋",
    layout="wide",
    initial_sidebar_state="expanded",
)

def apply_dark_theme() -> None:
    st.markdown(
        """
        <style>
        .stApp { background:#1e1e2d; color:#e8e8e8; }
        .metric-card { background:#181825; border-radius:8px; padding:16px; border:1px solid #3a3a4e; }
        .metric-value{font-size:2rem;font-weight:bold;font-family:'Roboto',sans-serif;}
        .metric-label{font-size:.85rem;text-transform:uppercase;letter-spacing:.5px;color:#8a8a9e;}
        .icon{font-size:24px;margin-bottom:8px;}
        </style>
        """,
        unsafe_allow_html=True,
    )

apply_dark_theme()

# ---------------------------------------------------------------------------
# Helper – metric card
# ---------------------------------------------------------------------------
def metric_card(title: str, value: str, icon: str, color: str = "#ffffff") -> None:
    st.markdown(
        f"""
        <div class="metric-card" style="color: {color}">
            <div class="icon">{icon}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-label">{title}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Helper – power‑balance bar
# ---------------------------------------------------------------------------
def power_flow_bar(solar: float, load: float, grid: float) -> None:
    total = solar + load
    solar_pct = (solar / total * 100) if total > 0 else 0
    load_pct = (load / total * 100) if total > 0 else 0
    export_pct = max(0, grid / 10)  # arbitrary scaling

    st.markdown(
        f"""
        <div style="background:#181825;padding:12px;border-radius:8px;">
            <strong>Real‑time Power Balance</strong><br>
            <div style="margin-top:8px;">
                <div style="background:#f4a748;height:20px;border-radius:4px;width:{solar_pct}%;margin-bottom:4px;"></div>
                <div style="color:#f4a748;text-align:right;">Solar</div>
            </div>
            <div>
                <div style="background:#e76f51;height:20px;border-radius:4px;width:{load_pct}%;margin-bottom:4px;"></div>
                <div style="color:#e76f51;">Load</div>
            </div>
            <div>
                <div style="background:#7ec853;height:20px;border-radius:4px;width:{export_pct}%;margin-bottom:4px;"></div>
                <div style="color:#7ec853;">Export</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Sidebar – settings (placeholder, same as before)
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Settings")
st.sidebar.markdown("### 🔌 Connection Status")
st.sidebar.success("✅ Connected to InfluxDB")

with st.sidebar.expander("🔢 Data Settings"):
    hours_range = st.slider("Time Range", 1, 72, 12, help="Hours of data to display")
    st.sidebar.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

st.sidebar.markdown("---")
st.sidebar.markdown("### 📊 Tabs")
tab1, tab2, tab3 = st.tabs(["🔍 Real‑time", "📈 History", "⚙️ Settings"])

# ---------------------------------------------------------------------------
# Real‑time tab
# ---------------------------------------------------------------------------
with tab1:
    st.header("🌞 Real‑time Energy Overview")

    # Fetch current values
    solar_current = fetch_latest("Solar_Produced_Current")          # kW
    load_current = fetch_latest("Consumption_Current")              # kW
    grid_consumption_current = fetch_latest("Grid_Consumption_Current")  # kW
    grid_feedin_current = fetch_latest("Grid_FeedIn_Current")            # kW
    battery_soc = fetch_latest("Battery_SOC")                      # %
    battery_charging = fetch_latest("Battery_Charging")            # kW
    battery_discharging = fetch_latest("Battery_Discharging")      # kW

    # Net grid flow (positive → import, negative → export)
    grid_val = (grid_consumption_current or 0) - (grid_feedin_current or 0)

    # Totals for the day (kWh)
    solar_total = fetch_latest("Solar_Produced_Total")            # kWh
    consumption_total = fetch_latest("Consumption_Total")         # kWh
    grid_consumption_total = fetch_latest("Grid_Consumption_Total")  # kWh
    grid_feedin_total = fetch_latest("Grid_FeedIn_Total")         # kWh

    # -----------------------------------------------------------------------
    # First row – core metrics
    # -----------------------------------------------------------------------
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        metric_card(
            title="Solar Produced",
            value=f"{solar_current:.2f} kW" if solar_current is not None else "—",
            icon="☀️",
            color="#f4a748",
        )
    with col2:
        metric_card(
            title="House Load",
            value=f"{load_current:.2f} kW" if load_current is not None else "—",
            icon="🏠",
            color="#d485d4",
        )
    with col3:
        grid_str = f"-{abs(grid_val):.2f} kW" if grid_val < 0 else f"{grid_val:.2f} kW"
        grid_icon = "➡️" if grid_val < 0 else "⬅️"
        metric_card(
            title="Grid Flow",
            value=grid_str,
            icon=grid_icon,
            color="#7ec853" if grid_val < 0 else "#e76f51",
        )
    with col4:
        metric_card(
            title="Battery SOC",
            value=f"{battery_soc:.0f}%" if battery_soc is not None else "—",
            icon="🔋",
            color="#71b5ff",
        )
        # Battery charge/discharge summary
        with st.expander("📦 Battery Status"):
            st.markdown(
                f"""
                <div style="background:#181825;padding:16px;border-radius:8px;">
                    <div style="font-size:14px;color:#8a8a9e;">Charging Rate</div>
                    <div style="font-size:2rem;">{battery_charging:.2f} kW</div>
                    <div style="font-size:14px;color:#8a8a9e;margin-top:4px;">Discharging Rate</div>
                    <div style="font-size:2rem;">{battery_discharging:.2f} kW</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # -----------------------------------------------------------------------
    # Second row – total‑energy metrics
    # -----------------------------------------------------------------------
    col5, col6, col7, col8 = st.columns(4)

    with col5:
        metric_card(
            title="Total Energy Generated Today",
            value=f"{solar_total:.2f} kWh" if solar_total is not None else "—",
            icon="☀️",
            color="#f4a748",
        )
    with col6:
        metric_card(
            title="Total Energy Consumed Today",
            value=f"{consumption_total:.2f} kWh" if consumption_total is not None else "—",
            icon="🏠",
            color="#d485d4",
        )
    with col7:
        metric_card(
            title="Grid Consumption Today",
            value=f"{grid_consumption_total:.2f} kWh" if grid_consumption_total is not None else "—",
            icon="⬅️",
            color="#e76f51",
        )
    with col8:
        metric_card(
            title="Grid Feed‑In Today",
            value=f"{grid_feedin_total:.2f} kWh" if grid_feedin_total is not None else "—",
            icon="➡️",
            color="#7ec853",
        )

    # -----------------------------------------------------------------------
    # Power‑balance visualisation
    # -----------------------------------------------------------------------
    st.divider()
    power_flow_bar(
        solar=solar_current or 0,
        load=load_current or 0,
        grid=grid_val,
    )

    # -----------------------------------------------------------------------
    # Bottom totals – optional duplicates of the above
    # -----------------------------------------------------------------------
    st.divider()
    st.subheader("📊 Cumulative Totals")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            f"""
            <div style="background:#181825;padding:12px;border-radius:8px;text-align:center;">
                <div style="font-size:1.2rem;color:#f4a748;">{solar_total:.2f} kWh</div>
                <div style="font-size:0.8rem;color:#8a8a9e;">Solar Produced</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"""
            <div style="background:#181825;padding:12px;border-radius:8px;text-align:center;">
                <div style="font-size:1.2rem;color:#e76f51;">{consumption_total:.2f} kWh</div>
                <div style="font-size:0.8rem;color:#8a8a9e;">Total Load</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # Placeholder for a real chart
    st.markdown(
        """
        <div style="height:200px;background:#181825;border-radius:8px;display:flex;align-items:center;justify-content:center;">
            <span style="color:#8a8a9e;padding:16px;">[Line Chart Here]</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# History tab – placeholder
# ---------------------------------------------------------------------------
with tab2:
    st.header("📈 Historical Data")
    st.markdown("This section will display InfluxDB historical queries.")
    st.markdown("---")
    st.markdown(
        """
        <div style="height:300px;background:#181825;border-radius:8px;display:flex;align-items:center;justify-content:center;">
            <span style="color:#8a8a9e;padding:16px;">[7‑Day Energy Chart]</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Settings tab – placeholder
# ---------------------------------------------------------------------------
with tab3:
    st.header("⚙️ Application Settings")
    with st.expander("🔐 Connection Settings"):
        st.text_input("InfluxDB URL", value=INFLUX_URL)
        st.text_input("InfluxDB Token", type="password", value=INFLUX_TOKEN)
        st.text_input("Org", value=INFLUX_ORG)
        st.text_input("Bucket", value=INFLUX_BUCKET)
    st.divider()
    with st.expander("🔒 Security"):
        st.warning("⚠️ Ensure .env is in .gitignore before committing to VCS.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.markdown(
    """
    <div style="text-align:center;color:#8a8a9e;font-size:12px;">
        <strong>Fronius Energy Viewer</strong><br>
        Dark Theme • Muted Palette • Streamlit Based<br>
        Docker Deployment Ready
    </div>
    """,
    unsafe_allow_html=True,
)