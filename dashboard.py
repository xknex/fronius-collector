# ───────────────────────────────────────────────────────────────────────────────────────
#   dashboard.py – Fronius Energy Monitor (dark‑theme mockup, extended metrics)
# ───────────────────────────────────────────────────────────────────────────────────────
import streamlit as st
from datetime import datetime, timedelta
import pandas as pd

# --------------------------------------------------------------------------
# Configuration & theming
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Fronius Energy Monitor",
    page_icon="🔋",
    layout="wide",
    initial_sidebar_state="expanded",
)

def apply_dark_theme() -> None:
    """Add custom CSS for a dark theme with muted colours."""
    st.markdown(
        """
        <style>
        .stApp { background-color:#1e1e2d; color:#e8e8e8; }
        .metric-card { background:#181825; border-radius:8px; padding:16px; border:1px solid #3a3a4e; }
        .metric-value{font-size:2rem;font-weight:bold;font-family:'Roboto',sans-serif;}
        .metric-label{font-size:.85rem;text-transform:uppercase;letter-spacing:.5px;color:#8a8a9e;}
        .icon{font-size:24px;margin-bottom:8px;}
        </style>
        """,
        unsafe_allow_html=True,
    )

apply_dark_theme()

# --------------------------------------------------------------------------
# Mock data generator (replace with real InfluxDB queries later)
# --------------------------------------------------------------------------
def generate_mock_data() -> dict:
    """Return a dictionary containing mock PowerFlow data."""
    now = datetime.now()
    last_hour = now - timedelta(hours=1)
    minutes = [last_hour + timedelta(minutes=i) for i in range(60)]

    # Solar production – simple “sun‑curve”
    solar = [max(0, 2.5 + 1.5 * (1 - abs(i - 15) / 15)) for i in range(60)]
    # Load – a little oscillating
    load = [2.0 + 0.5 * (i % 2) for i in range(60)]
    # Battery state of charge
    soc = [95 - i % 5 for i in range(60)]
    # Grid power (export when solar > load)
    grid = [s - l for s, l in zip(solar, load)]

    total_solar = round(sum(solar), 2)
    total_load = round(sum(load), 2)
    total_export = round(sum(max(0, g) for g in grid), 2)
    total_import = round(sum(max(0, -g) for g in grid), 2)

    df_power = pd.DataFrame(
        {"timestamp": minutes, "solar_kw": solar, "load_kw": load, "grid_kw": grid}
    )
    df_solar = pd.DataFrame(
        {"timestamp": minutes, "production_kwh": [s * 0.1 for s in solar]}
    )

    return {
        "power": df_power,
        "solar_total": total_solar,
        "load_total": total_load,
        "export_total": total_export,
        "import_total": total_import,
        "soc": round(soc[-1], 0),
        "batt_charging": max(0, -sum(grid[-10:]) / 10) / 100,  # kW
        "batt_discharging": max(0, sum(grid[-10:]) / 10) / 100,  # kW
    }

# --------------------------------------------------------------------------
# Helper: metric card
# --------------------------------------------------------------------------
def metric_card(title: str, value: str, icon: str, color: str = "#ffffff") -> None:
    """Render a single metric card."""
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

# --------------------------------------------------------------------------
# Helper: power‑balance bar
# --------------------------------------------------------------------------
def power_flow_bar(solar: float, load: float, grid: float) -> None:
    """Show a simple horizontal bar visualising solar, load and export."""
    total = solar + load
    solar_pct = (solar / total * 100) if total > 0 else 0
    load_pct = (load / total * 100) if total > 0 else 0
    export_pct = max(0, grid / 10)  # arbitrary scaling for visual

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

# --------------------------------------------------------------------------
# Sidebar – simple settings (placeholder)
# --------------------------------------------------------------------------
st.sidebar.title("⚙️ Settings")
st.sidebar.markdown("### 🔌 Connection Status")
st.sidebar.success("✅ Connected to InfluxDB")

with st.sidebar.expander("🔢 Data Settings"):
    hours_range = st.slider("Time Range", 1, 72, 12, help="Hours of data to display")
    st.sidebar.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

st.sidebar.markdown("---")
st.sidebar.markdown("### 📊 Tabs")
tab1, tab2, tab3 = st.tabs(["🔍 Real‑time", "📈 History", "⚙️ Settings"])

# --------------------------------------------------------------------------
# Real‑time tab
# --------------------------------------------------------------------------
with tab1:
    st.header("🌞 Real‑time Energy Overview")

    # Generate the mock data
    data = generate_mock_data()

    # ------------------------------------------------------------------
    # First row – core metrics
    # ------------------------------------------------------------------
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        metric_card(
            title="Solar Produced",
            value=f"{data['solar_total']} kW",
            icon="☀️",
            color="#f4a748",
        )

    with col2:
        metric_card(
            title="House Load",
            value=f"{data['load_total']} kW",
            icon="🏠",
            color="#d485d4",
        )

    # Grid power – use positional indexing
    grid_val = data["power"]["grid_kw"].iloc[-1]
    with col3:
        grid_str = f"-{grid_val:.2f} kW" if grid_val < 0 else f"{grid_val:.2f} kW"
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
            value=f"{data['soc']}%",
            icon="🔋",
            color="#71b5ff",
        )
        # Battery charge/discharge summary
        with st.expander("📦 Battery Status"):
            st.markdown(
                f"""
                <div style="background:#181825;padding:16px;border-radius:8px;">
                    <div style="font-size:14px;color:#8a8a9e;">Charging Rate</div>
                    <div style="font-size:2rem;">{data['batt_charging']:.2f} kW</div>
                    <div style="font-size:14px;color:#8a8a9e;margin-top:4px;">Discharging Rate</div>
                    <div style="font-size:2rem;">{data['batt_discharging']:.2f} kW</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ------------------------------------------------------------------
    # Second row – total‑energy metrics
    # ------------------------------------------------------------------
    col5, col6, col7, col8 = st.columns(4)

    with col5:
        metric_card(
            title="Total Energy Generated Today",
            value=f"{data['solar_total']} kWh",
            icon="☀️",
            color="#f4a748",
        )
    with col6:
        metric_card(
            title="Total Energy Consumed Today",
            value=f"{data['load_total']} kWh",
            icon="🏠",
            color="#d485d4",
        )
    with col7:
        metric_card(
            title="Grid Consumption Today",
            value=f"{data['import_total']} kWh",
            icon="⬅️",
            color="#e76f51",
        )
    with col8:
        metric_card(
            title="Grid Feed‑In Today",
            value=f"{data['export_total']} kWh",
            icon="➡️",
            color="#7ec853",
        )

    # ------------------------------------------------------------------
    # Power‑balance visualisation
    # ------------------------------------------------------------------
    st.divider()
    power_flow_bar(
        solar=data["power"]["solar_kw"].iloc[-1],
        load=data["power"]["load_kw"].iloc[-1],
        grid=grid_val,
    )

    # ------------------------------------------------------------------
    # Bottom totals (duplicate of some cards – optional)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📊 Cumulative Totals")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            f"""
            <div style="background:#181825;padding:12px;border-radius:8px;text-align:center;">
                <div style="font-size:1.2rem;color:#f4a748;">{data['solar_total']} kWh</div>
                <div style="font-size:0.8rem;color:#8a8a9e;">Solar Produced</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"""
            <div style="background:#181825;padding:12px;border-radius:8px;text-align:center;">
                <div style="font-size:1.2rem;color:#e76f51;">{data['load_total']} kWh</div>
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

# --------------------------------------------------------------------------
# History tab – placeholder
# --------------------------------------------------------------------------
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

# --------------------------------------------------------------------------
# Settings tab – placeholder
# --------------------------------------------------------------------------
with tab3:
    st.header("⚙️ Application Settings")
    with st.expander("🔐 Connection Settings"):
        st.text_input("InfluxDB URL", value="http://your-influx-host:8086")
        st.text_input("InfluxDB Token", type="password")
        st.text_input("Org", value="fronius")
        st.text_input("Bucket", value="fronius_clean")
    st.divider()
    with st.expander("🔒 Security"):
        st.warning("⚠️ Ensure .env file is in .gitignore before committing to version control.")

# --------------------------------------------------------------------------
# Footer
# --------------------------------------------------------------------------
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