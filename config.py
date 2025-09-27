import logging
import os

from dotenv import load_dotenv
import prometheus_client
import structlog
from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics import _Gauge as Gauge
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry

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
    logger.debug("configured application telemetry successfully")


def setup_tracing(resource: Resource) -> TracerProvider:
    """Setup tracing within the application context to ensure reliable trace and span capture within FastAPI context, and to
    add the context to the logger for further distributed tracing. This is necessary else we do not see trace and span
    ID values in the middleware."""

    tracer_provider = TracerProvider(resource=resource)

    span_exporter = OTLPSpanExporter(endpoint=OTEL_BACKEND_ENDPOINT)
    span_processor = BatchSpanProcessor(span_exporter)
    tracer_provider.add_span_processor(span_processor)

    trace.set_tracer_provider(tracer_provider)
    return tracer_provider


def setup_logging(resource: Resource):
    """Enable log collection and processing through opentelemetry."""

    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)

    logger_exporter = OTLPLogExporter(endpoint=OTEL_BACKEND_ENDPOINT)
    logger_processor = BatchLogRecordProcessor(logger_exporter)
    logger_provider.add_log_record_processor(logger_processor)

    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))
    LoggingInstrumentor().instrument(set_logging_format=True)


def setup_metrics(resource: Resource) -> tuple[MeterProvider, tuple[Counter, Histogram, Gauge]]:
    """Enables metrics collection and processing through opentelemetry. Exposes distinct metrics instruments for use
    throughout the application."""

    metric_exporter = OTLPMetricExporter(endpoint=OTEL_BACKEND_ENDPOINT)
    otel_metric_reader = PeriodicExportingMetricReader(metric_exporter)

    metric_readers = [otel_metric_reader]
    if OBSERVABILITY_BACKEND == "prometheus":
        # register the prometheus specific reader that'll expose a /metrics endpoint usable by prometheus scrape job
        # this enables us to avoid rewriting metrics instruments for prometheus specifically
        prometheus_metric_reader = PrometheusMetricReader()
        metric_readers.append(prometheus_metric_reader)

        global prometheus_registry
        prometheus_registry = prometheus_client.REGISTRY

        logger.debug("configured Prometheus metric reader")

    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
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

    return meter_provider, (counter, histogram, gauge)


def instrument_fastapi(app: FastAPI):
    """Instruments FastAPI app instance exactly once."""

    # this is the same approach used by `instrument_app` method internally, to avoid multiple instrumentation attempts
    if not getattr(app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.instrument_app(app)
    return app
