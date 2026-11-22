from prometheus_client import (Counter, Gauge, Histogram,
                               disable_created_metrics)

disable_created_metrics()

SERVICE = "payments"

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests served",
    ["service", "route", "code"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration",
    ["service", "route"],
    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2, 5, 10))
CHARGES = Counter(
    "payments_charges", "Charges by final status", ["status"])
DUPLICATE_CHARGES = Counter(
    "payments_duplicate_charges",
    "Approved charges for an order that already had one")
CAPTURED_FOR_FAILED = Gauge(
    "payments_captured_for_failed_orders",
    "Approved, unrefunded charges whose order is cancelled or rejected")
PROVIDER_ERRORS = Counter(
    "card_provider_errors", "Failed calls to the card provider",
    ["reason"])
