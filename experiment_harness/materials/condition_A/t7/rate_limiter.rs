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
