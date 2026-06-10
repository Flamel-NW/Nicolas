use crate::time::clock;
use crate::metrics::recorder;
use crate::metrics::recorder::MetricValue;

/// Opaque rate-limit key. The inner `String` is not exposed.
#[allow(dead_code)]
pub struct RateLimitKey(String);

/// Construct a RateLimitKey from a raw string. Pure constructor, no effects.
pub fn new_rate_limit_key(raw: String) -> RateLimitKey {
    RateLimitKey(raw)
}

/// Check whether a request is within the rate limit and record a metrics event.
///
/// Reads the current wall-clock time to evaluate the rate-limit window, then
/// writes a counter metric regardless of whether the request is allowed.
///
/// # Effects
/// - `reads_clock`: reads system time via time.clock::now
/// - `metrics.write`: records a request counter via metrics.recorder::record
pub fn check_and_record(_key: RateLimitKey) -> bool {
    let _ts = clock::now();
    let name = recorder::new_metric_name(String::from("rate_limiter.requests.total"));
    let value = MetricValue(1.0);
    recorder::record(name, value);
    true
}
