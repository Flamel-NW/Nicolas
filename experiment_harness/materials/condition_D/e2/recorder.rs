// @nico-module: metrics.recorder
// @nico-intent: 提供 metrics 写入的唯一边界。所有需要记录指标的模块均通过此接口写入，避免其他模块直接触发 metrics.write effect。record 函数将 MetricName 与 MetricValue 一并写入底层 metrics 系统。
// @nico-imports:
// @nico-module-effects: metrics.write
// @nico-type: MetricName | pub | opaque
// @nico-type: MetricValue | pub | opaque
// @nico-fn: new_metric_name | pub fn new_metric_name(raw: String) -> MetricName | effects= | calls=
// @nico-fn: record | pub fn record(name: MetricName, value: MetricValue) -> () | effects=metrics.write | calls=

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
