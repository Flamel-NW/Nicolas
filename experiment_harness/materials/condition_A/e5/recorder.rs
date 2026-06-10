/// Opaque metric name. The inner `String` is not exposed to prevent callers
/// from constructing metric names without going through the designated constructor.
#[allow(dead_code)]
pub struct MetricName(String);

/// f64 newtype wrapper representing a metric value.
#[allow(dead_code)]
pub struct MetricValue(pub f64);

/// Construct a MetricName from a raw string. Pure constructor, no effects.
pub fn new_metric_name(raw: String) -> MetricName {
    MetricName(raw)
}

/// Write a metric to the underlying metrics system.
///
/// # Effects
/// - `metrics.write`: writes a (name, value) pair to the metrics backend.
pub fn record(_name: MetricName, _value: MetricValue) {
    todo!("metrics.recorder::record: real implementation pending")
}
