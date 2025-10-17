# Fronius GEN24 Data Collector 

A dockerized Python script for real-time data collection from Fronius GEN24 inverters, with support for InfluxDB, logging, and colorized output. 

**This docker image is aimed at people who don't want to share their solar production and consumption data with external services provided by the hardware vendor. The collected data can bes stored persistantly on your local infrastructure and visualized using Grafana for example.**

---

## 📌 Overview

This script collects real-time energy data from Fronius GEN24 inverters via HTTP API and sends the data to your local InfluxDB for storage and visualization. It includes:

- **Colorized terminal output** for immediate visual feedback.
- **Comprehensive logging** to a file for troubleshooting.
- **Accumulated totals** (kWh) for energy consumption, production, and grid interaction.
- **Battery metrics** (SOC, charging/discharging power).
- **Flexible configuration** via YAML file.
- **Polling interval** customization.

---

## 🛠️ Features

| Feature | Description |
|--------|-------------|
| **Real-Time Data Collection** | Fetches live data from Fronius inverters via HTTP API. |
| **InfluxDB Integration** | Sends data to InfluxDB for time-series storage and analysis. |
| **Colorized Output** | Highlights key metrics in the terminal (e.g., green for grid feed-in, red for consumption). |
| **Logging** | Detailed logs written to `collector.log` for troubleshooting. |
| **Configuration Management** | Uses a YAML file for InfluxDB and Fronius inverter settings. |
| **Polling Interval** | Customizable delay between data collection cycles. |
| **Unit Tags** | Stores data with units (e.g., kW, kWh, %) for clarity. |

---

## 📦 Prerequisites

- **Docker Engine and Docker Compose**
- **Fronius Inverter** (GEN24 or compatible model)
- **InfluxDB 2.x** (for time-series data storage)
  - An existing bucket (eg named "fronius)
  - An existing Access Key (API Key) with write privileges
- **Network Access**:
  - Between the script and the Fronius inverter (port 80/443).
  - Between the script and the InfluxDB server.

## 🛠️ Installing Requirements for Docker Compose (Linux)

To use Docker Compose, ensure **Docker Engine** and **Docker Compose** are installed on your system. Below are installation instructions for popular Linux distributions:

---

### ✅ Debian/Ubuntu

```bash
# Update package index
sudo apt update

# Install dependencies
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common

# Add Docker's official GPG key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg

# Add Docker repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Update package index again
sudo apt update

# Install Docker Engine and Docker Compose
sudo apt install -y docker-ce docker-ce-cli docker-compose
```

---

### ✅ Arch Linux

```bash
# Install Docker and Docker Compose using pacman
sudo pacman -S docker docker-compose
```

---

### ✅ Fedora

```bash
# Enable the COPR repository for Docker
sudo dnf copr enable docker/docker

# Install Docker Engine and Docker Compose
sudo dnf install -y docker docker-compose
```

---

### ✅ General Post-Installation Steps

After installation:

1. **Start and enable Docker service**:
   ```bash
   sudo systemctl start docker
   sudo systemctl enable docker
   ```

2. **Add your user to the `docker` group** (to avoid using `sudo`):
   ```bash
   sudo usermod -aG docker $USER
   ```
   > Log out and back in, or run `newgrp docker` to apply group changes.

3. **Verify installation**:
   ```bash
   docker --version
   docker-compose --version
   ```

---

### 📌 Notes

- Ensure your system is up to date before installing.
- If using a different Linux distribution, refer to the [official Docker documentation](https://docs.docker.com/engine/install/).`

### 🔧 Configuration Details

The container is configured for runtime with the following environment variables best declared in a separate .env file.
See example below.

## 📊 Data Structure

The script collects the following metrics, stored with units and data types:

| Field Name                  | Description                                 | Unit | Data Type |
|-----------------------------|---------------------------------------------|------|-----|
| `Battery_SOC`               | Battery state of charge                     | %    | float     |
| `Solar_Produced_Current`    | Current solar production                    | kW   | float     |
| `Consumption_Current`       | Current energy consumption                  | kW   | float     |
| `Grid_Consumption_Current`  | Current grid import                         | kW   | float     |
| `Grid_FeedIn_Current`       | Current grid export                         | kW   | float     |
| `Grid_FeedIn_Total`         | Total energy exported to grid               | kWh  | float     |
| `Grid_Consumption_Total`    | Total energy consumed from grid             | kWh  | float     |
| `Consumption_Total`         | Total energy consumed by site               | kWh  | float     |
| `Solar_Produced_Total`      | Total energy produced by solar              | kWh  | float     |
| `Autonomy_Percentage`       | Percentage of energy self-sufficiency       | %    | float     |
| `Logged_At`                 | Timestamp of data collection                | s    | int       |

---

## 📁 Logging

- **Log File**: `collector.log` is created in the working directory.
- **Content**: Includes script start/end, HTTP requests, data collection, and errors.
- **Example Log Entry**:

```
[2025-10-17/08:29:20] Solar=3.37 | Load=1.41 | Grid+0.03/-0.00kW | SOC=10.40% | Batt+1.87/-0.00kW | Auto=100.00% | ConsTot=2115.04kWh | GridConsTot=535.56kWh | GridFeedTot=2115.04kWh
[2025-10-17/08:29:30] Solar=3.37 | Load=1.42 | Grid+0.01/-0.00kW | SOC=10.40% | Batt+1.90/-0.00kW | Auto=100.00% | ConsTot=2115.04kWh | GridConsTot=535.56kWh | GridFeedTot=2115.04kWh
[2025-10-17/08:29:40] Solar=3.37 | Load=1.43 | Grid+0.00/-0.00kW | SOC=10.50% | Batt+1.89/-0.00kW | Auto=99.75% | ConsTot=2115.04kWh | GridConsTot=535.56kWh | GridFeedTot=2115.04kWh
[2025-10-17/08:29:50] Solar=3.37 | Load=1.42 | Grid+0.00/-0.00kW | SOC=10.50% | Batt+1.89/-0.00kW | Auto=100.00% | ConsTot=2115.04kWh | GridConsTot=535.56kWh | GridFeedTot=2115.04kWh
```

---

## 🛠️ Building and Using the Docker Image Locally

This section provides step-by-step instructions for **building** and **running** the `fronius-collector` Docker image using the provided `Dockerfile`, `.env` file, and `docker-compose.yaml`. It also includes notes on configuration, improvements, and potential issues.

---

### 📦 Prerequisites

Before proceeding, ensure the following tools are installed:

- **Docker Engine** (https://docs.docker.com/engine/install/)
- **Docker Compose** (https://docs.docker.com/compose/install/)

You can verify the installation with:

```bash
docker --version
docker-compose --version
```

---

### 🧱 Step 1: Build the Docker Image

You can build the Docker image either **manually** or **via Docker Compose**.

#### ✅ Option 1: Build with Docker CLI

```bash
# Pull repository from Github
git clone https://github.com/xknex/fronius-collector.git

# Navigate to the project directory
cd ./fronius-collector

# Build the Docker image
docker build -t fronius-collector .
```

#### ✅ Option 2: Build with Docker Compose

```bash
# Build the image using docker-compose
docker-compose build
```

---

### 📁 Step 2: Create and Configure the `.env` File

Create a `.env` file in the project directory and customize the values according to your setup:

```env
# Inverter
FRONIUS_INVERTER_HOST=HOSTNAME_OR_IP
FRONIUS_INVERTER_USE_HTTPS=false
FRONIUS_INVERTER_VERIFY_SSL=false
FRONIUS_INVERTER_DEVICE_ID=1
# Influx
INFLUX_URL=http://HOSTNAME_OR_IP:8086
INFLUX_TOKEN=SECRET
INFLUX_ORG=org
INFLUX_BUCKET=fronius
# Polling/tags
POLLING_INTERVAL=10
TAG_SOURCE=SymoGEN24
TAG_SITE=home
# Extra tags (optional)
TAGS=location=home,env=prod
```

> ⚠️ Replace placeholders like `HOSTNAME_OR_IP` and `SECRET` with your actual InfluxDB and Fronius inverter details.

---

### 🚀 Step 3: Run the Services with Docker Compose

Once the `.env` file is set up, run the services using:

```bash
docker compose up -d
```

This will:

- Start the `fronius-collector` service (using the image built in Step 1)
- Start the `influxdb2` service (optional, can be removed if you have an existing InfluxDB instance)

> 📌 If you're not using InfluxDB, remove the `influxdb2` service from the `docker-compose.yaml` file.

---

### 📊 Step 4: Verify the Collector

- Log in to your InfluxDB web UI and use the Data Explorer to check the written data in your 'fronius' bucket
- Logs are stored in the local `./logs` directory (mounted as `/app/logs` inside the container).
- The healthcheck ensures the collector is running and responding.

You can check the logs with:

```bash
docker logs -f fronius-collector
```

---

## 🛠️ Troubleshooting

### ❗ Common Issues & Fixes

| Issue | Solution |
|------|------|
| **Connection to Fronius inverter fails** | Check network connectivity and inverter IP address. Ensure port 80/443 is open. |
| **InfluxDB write errors** | Verify InfluxDB URL, token, and bucket configuration. Test with `influx` CLI. |
| **No data in InfluxDB** | Ensure the script is running and logs are not showing errors. |
| **Colorized output not working** | Ensure terminal supports ANSI escape codes. Use `--no-color` flag if needed. |

---

## 📄 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for more details.

---

## 📌 Contributing

Contributions are welcome! Please submit a pull request or open an issue for suggestions or bug reports.

---

## 🔗 Links

- [GitHub Repository](https://github.com/xknex/frondius-collector)
- [InfluxDB Documentation](https://docs.influxdata.com/)
- [Fronius Inverter API Docs](https://www.fronius.com/en/products/solar-inverters)