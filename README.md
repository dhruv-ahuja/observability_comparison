# Observability Comparison PoC

## Introduction

Observability Setup and Value Comparison PoC between SigNoz and (Prometheus, Grafana, and Loki) stack. This setup enables user to compare the effort requirements, ease of use, maintainability and overall value between different self-hosted observability stacks.

Stack A is SigNoz, built on OpenTelemetry standards, enabling end-to-end observability out of the box.
Stack B uses Prometheus, Grafana and Loki and requires additional setup to enable metrics and logs coverage.

## Usage Guide

### Prerequisites

You must have Docker (with Docker Compose support) installed, and [host networking]((https://docs.docker.com/desktop/features/networking/#i-want-to-connect-from-a-container-to-a-service-on-the-host)) enabled inside Docker for the Prometheus-Grafana-Loki setup. This is necessary for Prometheus to communicate with the application running on host machine network.

### Application Setup

Create a virtual environment using Python version >= 3.11:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

You are now ready to start the application based on the observability stack of your choice.

### SigNoz Setup

Start the Python application:

```bash
opentelemetry-instrument python main.py
```

Start the self-hosted Signoz stack:

```bash
docker compose -f signoz/deploy/docker/docker-compose.yaml up -d --remove-orphans
```

Access the SigNoz application at `http://localhost:8084`, create login credentials and you can now start using it for your observability needs.

![SigNoz Onboarding View](img/signoz_onboarding.png)

### Prometheus-Grafana-Loki Setup

Start the Python application:

```bash
OPENTELEMETRY_BACKEND=grafana-stack opentelemetry-instrument python main.py
```

Start the self-hosted Prometheus-Grafana-Loki stack:

```bash
docker compose -f grafana-stack/docker-compose.yaml up -d
```

Visit `http://localhost:9090/targets?search=` to access the Prometheus application, and check if all the targets are being scraped from. Refresh the page as it may take some time.

Once done, you should see all targets being up:

![Prometheus Targets Page](img/prometheus_targets.png)

Access the Grafana application at `http://localhost:3000`, login with `admin` as username and password.

#### Integrating Data Sources into Grafana

Next, select `Data Sources` from the left navigation menu, and click on `Add data source`.
Select `Prometheus` as the data source type, and enter `http://localhost:9090` as the URL.
Click on `Save & Test` to save the data source.

Select `Data Sources` from the left navigation menu, and click on `Add data source`.
Select `Loki` as the data source type, and enter `http://localhost:3100` as the URL.
Click on `Save & Test` to save the data source.

You have now configured `Loki` for logs and `Prometheus` for metrics ingestion, for visualization in Grafana, and can now start using it for your observability needs.

### Stopping the Services

To stop the SigNoz stack:
```bash
docker compose -f signoz/deploy/docker/docker-compose.yaml down
```

To stop the Prometheus-Grafana-Loki stack:
```bash
docker compose -f grafana-stack/docker-compose.yaml down
```
