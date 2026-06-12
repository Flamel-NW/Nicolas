/// Opaque metric name. The inner `String` is not exposed to prevent callers
/// from constructing names without going through a designated constructor.
#[allow(dead_code)]
pub struct MetricName(String);

/// Newtype wrapper around f64 representing a metric value.
#[allow(dead_code)]
pub struct MetricValue(pub f64);

/// Construct a MetricName from a raw string.
pub fn new_metric_name(raw: String) -> MetricName {
    MetricName(raw)
}

/// Record a metric by writing the given name and value to the metrics backend.
pub fn record(_name: MetricName, _value: MetricValue) {
    todo!("metrics.recorder::record: real implementation pending")
}
