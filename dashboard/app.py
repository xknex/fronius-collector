from flask import Flask, render_template, jsonify
from dotenv import load_dotenv
import os
from influxdb_client import InfluxDBClient, Point, WritePrecision
from datetime import datetime, timedelta

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
INFLUX_URL = os.getenv("INFLUX_URL")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")

app = Flask(__name__)

# Initialize InfluxDB Client
try:
    client = InfluxDBClient(
        url=INFLUX_URL, 
        token=INFLUX_TOKEN, 
        org=INFLUX_ORG
    )
    write_api = client.write_api()
    print("Successfully connected to InfluxDB.")
except Exception as e:
    print(f"Error connecting to InfluxDB: {e}")
    client = None # Set client to None if connection fails

@app.route('/')
def index():
    """Renders the main dashboard page."""
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    """
    Fetches aggregated energy data from InfluxDB for charting.
    Returns data as a JSON object suitable for Chart.js.
    """
    if not client:
        return jsonify({"error": "Database connection failed."}), 503

    # Define the time range (e.g., last 24 hours)
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=24)
    
    # Flux query to fetch key metrics over the last 24 hours
    # This query assumes the data structure from collector.py is consistent.
    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: {start_time.isoformat()}Z, stop: {end_time.isoformat()}Z)
      |> filter(fn: (r) => r["_measurement"] == "fronius_clean")
      |> filter(fn: (r) => r["_field"] == "Solar_Produced_Current" or r["_field"] == "Consumption_Current" or r["_field"] == "Grid_Consumption_Current")
      |> aggregateWindow(every: 1h, fn: mean, createEmpty: true)
      |> yield(name: "PowerFlow")
    '''
    
    try:
        # Query the data
        query_api = client.query_api()
        result = query_api.query(query=flux_query)
        
        # Process results into a format suitable for Chart.js
        data_points = {}
        
        # Simple aggregation for demonstration: we'll just collect the last point for simplicity
        # In a real scenario, you'd process all points for time-series charts.
        
        # For this example, we will return a placeholder structure that the JS will interpret.
        # A more complex implementation would iterate through the Flux results.
        
        # Placeholder data structure:
        return jsonify({
            "success": True,
            "labels": [start_time.strftime('%Y-%m-%d %H:%M'), (end_time - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M'), end_time.strftime('%Y-%m-%d %H:%M')],
            "datasets": [
                {
                    "label": "Solar Production (kW)",
                    "data": [0, 0, 0], # Placeholder data
                    "borderColor": "rgba(255, 205, 86, 1)",
                    "backgroundColor": "rgba(255, 205, 86, 0.2)",
                    "yAxisID": "y"
                },
                {
                    "label": "Consumption (kW)",
                    "data": [0, 0, 0], # Placeholder data
                    "borderColor": "rgba(255, 99, 132, 1)",
                    "backgroundColor": "rgba(255, 99, 132, 0.2)",
                    "yAxisID": "y"
                }
            ]
        })
    except Exception as e:
        print(f"Error querying InfluxDB: {e}")
        return jsonify({"error": f"Failed to query data: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)