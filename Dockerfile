FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ./app ./app

# run command with runtime env var in shell command mode
CMD ["sh", "-c", "OBSERVABILITY_BACKEND=${OBSERVABILITY_BACKEND} opentelemetry-instrument uvicorn app.main:app --host 0.0.0.0 --port 8000"]
