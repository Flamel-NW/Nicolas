// @nico-module: metrics.recorder
// @nico-intent: 提供 metrics 写入的唯一边界。所有需要记录指标的模块均通过此接口写入，避免其他模块直接触发 metrics.write effect。record 函数将 MetricName 与 MetricValue 一并写入底层 metrics 系统。
// @nico-imports:
// @nico-module-effects: metrics.write
// @nico-type: MetricName | pub | opaque
// @nico-type: MetricValue | pub | opaque
// @nico-fn: new_metric_name | pub fn new_metric_name(raw: String) -> MetricName | effects= | calls=
// @nico-fn: record | pub fn record(name: MetricName, value: MetricValue) -> () | effects=metrics.write | calls=

/// 不透明度量名称。内部 `String` 不对外暴露。
#[allow(dead_code)]
pub struct MetricName(String);

/// f64 的 newtype wrapper，表示一个度量值。
#[allow(dead_code)]
pub struct MetricValue(pub f64);

/// 从原始字符串构造 MetricName。纯函数，无任何 effects。
pub fn new_metric_name(_raw: String) -> MetricName {
    todo!()
}

/// 将 (name, value) 写入底层 metrics 系统。
///
/// Effects: metrics.write
pub fn record(_name: MetricName, _value: MetricValue) {
    todo!()
}
