/// Opaque cache key. The inner `String` is not exposed to prevent callers
/// from constructing keys without going through a designated constructor.
#[allow(dead_code)]
pub struct CacheKey(String);

/// TTL duration in seconds.
#[allow(dead_code)]
pub struct CacheTtl(u64);

/// Look up a cache entry by key.
///
/// Checks whether the stored entry has expired. Returns `None` if the key
/// is absent or the entry has expired.
pub fn get(_key: CacheKey) -> Option<String> {
    todo!()
}

/// Insert or overwrite a cache entry with the given TTL.
///
/// Records a timestamp alongside the value so that `get` can compute
/// when the entry expires.
pub fn set(_key: CacheKey, _value: String, _ttl: CacheTtl) {
    todo!()
}

/// Remove a cache entry unconditionally.
pub fn delete(_key: CacheKey) {
    todo!()
}
