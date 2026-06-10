// @nico-module: metrics.recorder
// @nico-intent: 提供 metrics 写入的唯一边界。所有模块通过此接口写入指标，避免 metrics.write effect 散落于业务层。new_metric_name 为纯构造函数；record 是唯一产生 metrics.write effect 的函数。
// @nico-imports:
// @nico-module-effects: metrics.write
// @nico-type: MetricName | pub | opaque
// @nico-type: MetricValue | pub | newtype
// @nico-fn: new_metric_name | pub fn new_metric_name(raw: String) -> MetricName | effects= | calls=
// @nico-fn: record | pub fn record(name: MetricName, value: MetricValue) -> () | effects=metrics.write | calls=

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
