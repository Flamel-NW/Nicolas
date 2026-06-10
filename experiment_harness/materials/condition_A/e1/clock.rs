/// Opaque wall-clock timestamp (internal precision: microseconds).
///
/// The internal representation is intentionally not exposed so that
/// downstream code cannot construct arbitrary `Timestamp` values
/// without going through `now()`.
#[allow(dead_code)]
pub struct Timestamp(u64);

/// A duration of time (internal precision: microseconds).
///
/// Kept opaque for the same reason as `Timestamp`.
#[allow(dead_code)]
pub struct Duration(u64);

/// Returns the current wall-clock time.
///
/// # Notes
/// This is a skeleton implementation. The real implementation will call
/// the OS time API and return a `Timestamp` wrapping the microseconds
/// since the Unix epoch.
pub fn now() -> Timestamp {
    todo!()
}
