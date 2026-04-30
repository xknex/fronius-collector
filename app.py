#!/usr/bin/env python3
"""Lightweight InfluxDB proxy for Fronius Dashboard"""
import os
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from influxdb_client import InfluxDBClient
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Fronius Dashboard API")

# Serve static frontend
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
def dashboard():
    return FileResponse("index.html")

@app.get("/api/data")
async def get_power_data(range_hours: int = Query(default=24, ge=1, le=720)):
    """Query InfluxDB with automatic downsampling for smooth charts"""
    client = InfluxDBClient(
        url=os.getenv("INFLUX_URL"),
        token=os.getenv("INFLUX_TOKEN"),
        org=os.getenv("INFLUX_ORG")
    )
    
    # Flux query: fetch last N hours, downsample to 5-min averages
    flux_query = f'''
        from(bucket: "fronius_clean")
          |> range(start: -{range_hours}h)
          |> filter(fn: (r) => r._measurement == "fronius_clean")
          |> filter(fn: (r) => r._field =~ /Solar_Produced_Current|Consumption_Current|Grid_Consumption_Current|Grid_FeedIn_Current/)
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> aggregateWindow(every: 5m, fn: mean)
          |> sort(columns: ["_time"])
    '''
    
    result = client.query_api().query(query=flux_query)
    
    # Transform InfluxDB response to flat JSON for frontend
    data = {"labels": [], "solar": [], "load": [], "grid_in": [], "grid_out": []}
    time_groups = {}
    
    for table in result:
        for record in table.records:
            ts = record.values["_time"].isoformat()
            field = record.values["_field"]
            val = float(record.values["_value"]) if record.values["_value"] is not None else 0.0
            
            if ts not in time_groups:
                time_groups[ts] = {}
            time_groups[ts][field] = val

    for ts, vals in sorted(time_groups.items()):
        data["labels"].append(ts)
        data["solar"].append(vals.get("Solar_Produced_Current", 0))
        data["load"].append(vals.get("Consumption_Current", 0))
        data["grid_in"].append(vals.get("Grid_Consumption_Current", 0))
        data["grid_out"].append(vals.get("Grid_FeedIn_Current", 0))

    client.close()
    return data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)