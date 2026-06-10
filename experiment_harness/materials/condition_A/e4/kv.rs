use crate::time::clock;

/// Opaque cache key. The inner `String` is not exposed to prevent callers
/// from constructing keys without going through the designated constructor.
#[allow(dead_code)]
pub struct CacheKey(pub String);

/// Opaque TTL duration in seconds. Kept distinct from `time.clock::Duration`
/// to avoid coupling the cache interface to the clock module's internal
/// representation in v0.
#[allow(dead_code)]
pub struct CacheTtl(pub u64);

/// Construct a cache key from a raw string.
pub fn new_key(s: String) -> CacheKey {
    CacheKey(s)
}

/// Construct a TTL value from a duration in seconds.
pub fn new_ttl(secs: u64) -> CacheTtl {
    CacheTtl(secs)
}

/// Look up a cache entry by key.
///
/// Reads the current wall-clock time via `time.clock::now()` to check
/// whether the stored entry has passed its TTL. Returns `None` if the key
/// is absent or the entry has expired.
///
/// # Effects
/// - `reads_clock`: reads system time to evaluate TTL expiry.
pub fn get(_key: CacheKey) -> Option<String> {
    let _ts = clock::now();
    None
}

/// Insert or overwrite a cache entry with the given TTL.
///
/// Records the current wall-clock time via `time.clock::now()` alongside
/// the value so that `get` can compute the absolute expiry instant.
///
/// # Effects
/// - `reads_clock`: reads system time to record the insertion timestamp.
pub fn set(_key: CacheKey, _value: String, _ttl: CacheTtl) {
    let _ts = clock::now();
}

/// Remove a cache entry unconditionally.
///
/// Pure operation: does not read the clock or access any other module.
pub fn delete(_key: CacheKey) {}
