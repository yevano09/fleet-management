from prometheus_client import Counter, Histogram, Gauge

aegis_scrape_duration = Histogram(
    "aegis_scrape_duration_seconds",
    "Duration of Aegis metric scrape cycles",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0),
)

aegis_signals_total = Counter(
    "aegis_signals_total",
    "Total signals classified by the Aegis engine",
    ["severity", "metric"],
)

aegis_decisions_total = Counter(
    "aegis_decisions_total",
    "Total decisions made by the Aegis rule engine",
    ["rule", "decision"],
)

aegis_remediations_total = Counter(
    "aegis_remediations_total",
    "Total remediation actions executed",
    ["action", "status"],
)

aegis_remediation_duration = Histogram(
    "aegis_remediation_duration_seconds",
    "Duration of remediation action execution",
    ["action"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

aegis_dlq_depth = Gauge("aegis_dlq_depth", "Number of entries in the dead-letter queue")

aegis_active_remediations = Gauge("aegis_active_remediations", "Number of in-progress remediation actions")
