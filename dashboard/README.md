# Fronius Energy Dashboard

A modern, dark-themed, interactive web-based dashboard for visualizing Fronius GEN24 solar energy system data.

## Features

✨ **Modern Dark UI** - Sleek, eye-friendly dark theme with neon accents
📊 **Real-time Metrics** - Live updates of solar production, consumption, battery state, and more
📈 **Interactive Charts** - 24-hour power flow, energy distribution, battery trends
📱 **Responsive Design** - Works on desktop, tablet, and mobile devices
🚀 **Lightweight** - Single HTML file with Chart.js (no heavy frameworks)
🔗 **API-ready** - FastAPI backend for easy integration with InfluxDB

## Quick Start

### Option 1: Standalone HTML (Mock Data)
Simply open `index.html` in your web browser. Shows sample data for preview purposes.

```bash
cd dashboard
open index.html  # macOS
xdg-open index.html  # Linux
start index.html  # Windows
```

### Option 2: With FastAPI Backend

1. **Install dependencies:**
   ```bash
   cd dashboard
   pip install -r requirements.txt
   ```

2. **Run the server:**
   ```bash
   python app.py
   ```
   
   Or with environment variables:
   ```bash
   export DASHBOARD_PORT=8080
   python app.py
   ```

3. **Access the dashboard:**
   Open http://localhost:8080 in your browser

## Project Structure

```
dashboard/
├── index.html          # Main dashboard (standalone mockup)
├── app.py             # FastAPI backend server
├── requirements.txt   # Python dependencies
└── README.md         # This file
```

## Integration with InfluxDB

The backend (`app.py`) has placeholder endpoints for InfluxDB integration:

- `/api/data/current` - Current real-time metrics
- `/api/data/24h` - 24-hour historical data
- `/api/data/7d` - 7-day historical data

To enable actual data collection:

1. Update the endpoints in `app.py` to query your InfluxDB instance
2. Example query structure:
   ```python
   from influxdb_client import InfluxDBClient
   
   client = InfluxDBClient(
       url=os.getenv("INFLUX_URL"),
       token=os.getenv("INFLUX_TOKEN"),
       org=os.getenv("INFLUX_ORG")
   )
   query_api = client.query_api()
   # Query your fronius_clean bucket
   ```

3. Update the frontend (`index.html`) to fetch data from the API instead of using mock data

## Configuration

For the FastAPI backend, set environment variables:

```bash
export INFLUX_URL="http://localhost:8086"
export INFLUX_TOKEN="your-influxdb-token"
export INFLUX_ORG="your-org"
export INFLUX_BUCKET="fronius_clean"
export DASHBOARD_PORT="8080"
```

## Docker Deployment

Create a `Dockerfile` to containerize the dashboard:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "app.py"]
```

Build and run:
```bash
docker build -t fronius-dashboard .
docker run -p 8080:8080 -e INFLUX_URL=http://influxdb:8086 fronius-dashboard
```

## Customization

### Change Theme Colors
Edit the CSS variables in `index.html` (lines 14-21):

```css
:root {
    --primary: #00d9ff;      /* Cyan */
    --accent: #ff006e;       /* Pink */
    --warning: #ffa500;      /* Orange */
    --success: #00ff88;      /* Green */
    /* ... etc ... */
}
```

### Modify Charts
Update the chart configurations in the JavaScript section of `index.html` to match your InfluxDB data structure.

## Endpoints Reference

### Frontend
- `GET /` - Main dashboard HTML

### API
- `GET /api/health` - Health check
- `GET /api/data/current` - Current metrics (Solar, Consumption, Grid, Battery, etc.)
- `GET /api/data/24h` - 24-hour power flow data
- `GET /api/data/7d` - 7-day battery SOC trend data

## Browser Compatibility

- Chrome/Chromium 90+
- Firefox 88+
- Safari 14+
- Edge 90+

## Performance

- Dashboard size: ~30KB (index.html)
- Dependencies: Only Chart.js (loaded from CDN)
- Data refresh rate: Configurable (default: 10 seconds)
- API response time: < 100ms

## Future Enhancements

- [ ] Real-time WebSocket updates
- [ ] Export data to CSV/PDF
- [ ] Custom date range selection
- [ ] System alerts and notifications
- [ ] Multi-site dashboard support
- [ ] Mobile app PWA version
- [ ] Historical data comparison

## License

Same as the Fronius Collector project

## Support

For issues or feature requests, refer to the main Fronius Collector repository.
