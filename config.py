import logging
import os
from typing import Optional

from dotenv import load_dotenv
import prometheus_client
import structlog
from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# ---- Config Vars -----

load_dotenv()


OTEL_BACKEND_ENDPOINT = os.getenv("OTEL_BACKEND_ENDPOINT", "http://localhost:4317")
OBSERVABILITY_BACKEND = os.getenv("OBSERVABILITY_BACKEND", "signoz")


# ---- Logging -----

logging.basicConfig(
    level=logging.DEBUG,
    # use otel-friendly formatting
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def register_otel_ids(_, __, event_dict):
    """Registers trace and span IDs in structlog contextvars, enabling context propagation throughout application."""

    span = trace.get_current_span()
    context = span.get_span_context()
    if span.is_recording():
        # ensure trace and span IDs are in same format as in UI
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


structlog.configure(
    processors=[
        # merge_contextvars ensures that our bound contextvars are added to log entries as attributes
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        register_otel_ids,
        structlog.processors.JSONRenderer(),
    ],
    # ensure structlog uses logger objects to emit logs, enabling otel log collector to push them to the
    # observability backend
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


# ----- Telemetry -----

telemetry_configured = False

# NOTE: ideally we would use DI or singleton pattern class to maintain state
prometheus_registry = None

counter = None
histogram = None
gauge = None


def setup_telemetry():
    """Sets up telemetry for the application, including tracing, logging, and metrics based on observability backend
    in use. This ensures services are instrumented in their supported native manner with clean separation of
    concerns."""

    # ensure global boolean flag to prevent warnings from provider override attempts
    global telemetry_configured
    if telemetry_configured:
        return

    resource = Resource(attributes={"service.name": "python_app"})

    setup_tracing(resource)
    setup_logging(resource)
    _, metrics_instruments = setup_metrics(resource)

    global counter, histogram, gauge
    counter, histogram, gauge = metrics_instruments

    telemetry_configured = True
    logger.debug("configured application telemetry successfully", backend=OBSERVABILITY_BACKEND)


def setup_tracing(resource: Resource) -> Optional[TracerProvider]:
    """Setup tracing within the application context to ensure reliable trace and span capture within FastAPI context, and to
    add the context to the logger for further distributed tracing. This is necessary else we do not see trace and span
    ID values in the middleware."""

    if OBSERVABILITY_BACKEND != "signoz":
        return

    tracer_provider = TracerProvider(resource=resource)

    span_exporter = OTLPSpanExporter(endpoint=OTEL_BACKEND_ENDPOINT)
    span_processor = BatchSpanProcessor(span_exporter)
    tracer_provider.add_span_processor(span_processor)

    trace.set_tracer_provider(tracer_provider)
    return tracer_provider


def setup_logging(resource: Resource):
    """Enable log collection and processing through opentelemetry."""

    if OBSERVABILITY_BACKEND != "signoz":
        # Add file handler for Promtail to scrape and pass logs to Loki
        file_handler = logging.FileHandler("app.log")
        formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

        file_handler.setFormatter(formatter)
        logging.getLogger().addHandler(file_handler)

        return

    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)

    logger_exporter = OTLPLogExporter(endpoint=OTEL_BACKEND_ENDPOINT)
    logger_processor = BatchLogRecordProcessor(logger_exporter)
    logger_provider.add_log_record_processor(logger_processor)

    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))
    LoggingInstrumentor().instrument(set_logging_format=True)


def setup_metrics(resource: Resource) -> tuple[Optional[MeterProvider], tuple]:
    """Enables metrics collection and processing. Exposes distinct metrics instruments for use
    throughout the application. Uses native Prometheus metrics when OBSERVABILITY_BACKEND is 'prometheus'
    and OpenTelemetry metrics for other backends."""

    if OBSERVABILITY_BACKEND != "signoz":
        # Setup native Prometheus metrics without otel
        global prometheus_registry
        # registry is thread safe, it gathers metrics in memory and provides output in required formats
        prometheus_registry = prometheus_client.REGISTRY

        labelnames = ["method", "path"]
        counter = prometheus_client.Counter(
            "http_requests_total", "Total number of HTTP requests", registry=prometheus_registry, labelnames=labelnames
        )
        histogram = prometheus_client.Histogram(
            "http_request_duration_seconds",
            "Duration of HTTP requests",
            registry=prometheus_registry,
            labelnames=labelnames,
        )
        gauge = prometheus_client.Gauge(
            "active_users", "Number of active users", registry=prometheus_registry, labelnames=labelnames
        )

        logger.debug("configured Prometheus native metrics")
        return None, (counter, histogram, gauge)

    metric_exporter = OTLPMetricExporter(endpoint=OTEL_BACKEND_ENDPOINT)
    otel_metric_reader = PeriodicExportingMetricReader(metric_exporter)

    meter_provider = MeterProvider(resource=resource, metric_readers=[otel_metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = meter_provider.get_meter(name="python_app_metrics")

    counter = meter.create_counter(
        name="http_requests_total",
        unit="1",
        description="Total number of HTTP requests",
    )
    histogram = meter.create_histogram(
        name="http_request_duration_seconds",
        unit="s",
        description="Duration of HTTP requests",
    )
    gauge = meter.create_gauge(
        name="active_users",
        unit="1",
        description="Number of active users",
    )

    logger.debug("configured OpenTelemetry metrics")
    return meter_provider, (counter, histogram, gauge)


def increment_counter(amount, attributes):
    """Increment the counter by the specified amount."""

    global counter
    if OBSERVABILITY_BACKEND == "signoz":
        counter.add(amount, attributes)
    else:
        counter.labels(**attributes).inc(amount)


def record_histogram(value, attributes):
    """Record a value in the histogram."""

    global histogram
    if OBSERVABILITY_BACKEND == "signoz":
        histogram.record(value, attributes)
    else:
        histogram.labels(**attributes).observe(value)


def set_gauge(value, attributes):
    """Set the gauge to the specified value."""

    global gauge
    if OBSERVABILITY_BACKEND == "signoz":
        gauge.set(value, attributes)
    else:
        gauge.labels(**attributes).set(value)


def instrument_fastapi(app: FastAPI):
    """Instruments FastAPI app instance exactly once."""

    # this is the same approach used by `instrument_app` method internally, to avoid multiple instrumentation attempts
    if not getattr(app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.instrument_app(app)
    return app
