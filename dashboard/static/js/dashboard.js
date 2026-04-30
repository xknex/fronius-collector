// dashboard.js
document.addEventListener('DOMContentLoaded', () => {
    const powerFlowChartEl = document.getElementById('powerFlowChart');
    const energyTotalChartEl = document.getElementById('energyTotalChart');
    const solarValueEl = document.querySelector('#total-solar .value');
    const consumptionValueEl = document.querySelector('#total-consumption .value');
    const socValueEl = document.querySelector('#battery-soc .value');

    // --- Chart Initialization ---
    const powerFlowChart = new Chart(powerFlowChartEl, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Solar Production (kW)',
                    data: [],
                    borderColor: 'rgba(255, 205, 86, 1)',
                    backgroundColor: 'rgba(255, 205, 86, 0.2)',
                    yAxisID: 'y'
                },
                {
                    label: 'Consumption (kW)',
                    data: [],
                    borderColor: 'rgba(255, 99, 132, 1)',
                    backgroundColor: 'rgba(255, 99, 132, 0.2)',
                    yAxisID: 'y'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' }
                },
                y: {
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    title: { display: true, text: 'Power (kW)' }
                }
            },
            plugins: {
                legend: { labels: { color: '#e0e0e0' } }
            }
        }
    });

    const energyTotalChart = new Chart(energyTotalChartEl, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Solar Produced (kWh)',
                    data: [],
                    backgroundColor: 'rgba(255, 205, 86, 0.8)',
                },
                {
                    label: 'Total Consumption (kWh)',
                    data: [],
                    backgroundColor: 'rgba(255, 99, 132, 0.8)',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' }
                },
                y: {
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    title: { display: true, text: 'Energy (kWh)' }
                }
            },
            plugins: {
                legend: { labels: { color: '#e0e0e0' } }
            }
        }
    });

    // --- Data Fetching Function ---
    async function fetchData() {
        try {
            const response = await fetch('/api/data');
            const data = await response.json();

            if (data.error) {
                console.error("Error fetching data:", data.error);
                alert("Could not load data: " + data.error);
                return;
            }

            // 1. Update Charts
            updatePowerFlowChart(data);
            updateEnergyTotalChart(data);

            // 2. Update Summary Cards (using placeholder data for now)
            // In a real scenario, the backend would return these values directly.
            solarValueEl.textContent = "123.45"; // Placeholder
            consumptionValueEl.textContent = "56.78"; // Placeholder
            socValueEl.textContent = "85%"; // Placeholder

        } catch (error) {
            console.error("Network error fetching data:", error);
            alert("A network error occurred while connecting to the backend.");
        }
    }

    // --- Chart Update Functions ---
    function updatePowerFlowChart(data) {
        // Assuming data structure matches the placeholder in app.py
        const labels = data.labels;
        const solarData = data.datasets[0].data;
        const consumptionData = data.datasets[1].data;

        powerFlowChart.data.labels = labels;
        powerFlowChart.data.datasets[0].data = solarData;
        powerFlowChart.data.datasets[1].data = consumptionData;
        powerFlowChart.update();
    }

    function updateEnergyTotalChart(data) {
        // Assuming data structure matches the placeholder in app.py
        const labels = data.labels;
        const solarData = data.datasets[0].data;
        const consumptionData = data.datasets[1].data;

        energyTotalChart.data.labels = labels;
        energyTotalChart.data.datasets[0].data = solarData;
        energyTotalChart.data.datasets[1].data = consumptionData;
        energyTotalChart.update();
    }

    // Initial load and set interval for auto-refresh
    fetchData();
    setInterval(fetchData, 60000); // Refresh data every 60 seconds
});