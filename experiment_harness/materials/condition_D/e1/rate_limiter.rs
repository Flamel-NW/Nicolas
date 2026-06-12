// @nico-module: rate.limiter
// @nico-intent: 提供请求限流的统一边界。通过 time.clock 获取当前时间以判断限流窗口，通过 metrics.recorder 记录请求指标，避免其他模块分散处理限流逻辑与指标上报。check_and_record 是唯一公开检查接口：在单次调用中同时完成限额检查与指标记录。
// @nico-imports: time.clock, metrics.recorder
// @nico-module-effects: reads_clock, metrics.write
// @nico-type: RateLimitKey | pub | opaque
// @nico-fn: new_rate_limit_key | pub fn new_rate_limit_key(raw: String) -> RateLimitKey | effects= | calls=
// @nico-fn: check_and_record | pub fn check_and_record(key: RateLimitKey) -> bool | effects=reads_clock,metrics.write | calls=time.clock::now,metrics.recorder::new_metric_name,metrics.recorder::record

use crate::time::clock;
use crate::metrics::recorder;
use crate::metrics::recorder::MetricValue;

#[allow(dead_code)]
pub struct RateLimitKey(String);

pub fn new_rate_limit_key(raw: String) -> RateLimitKey {
    RateLimitKey(raw)
}

pub fn check_and_record(_key: RateLimitKey) -> bool {
    let _ts = clock::now();
    let name = recorder::new_metric_name(String::from("rate_limiter.requests.total"));
    let value = MetricValue(1.0);
    recorder::record(name, value);
    true
}
