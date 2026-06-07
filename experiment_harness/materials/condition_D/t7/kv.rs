// @nico-module: cache.kv
// @nico-intent: Provides a generic TTL-aware key-value cache boundary. All clock reads for TTL expiry checking are funnelled through time.clock, so no other module needs to read the clock to use the cache.
// @nico-imports: time.clock
// @nico-module-effects: reads_clock
// @nico-type: CacheKey | pub | opaque
// @nico-type: CacheTtl | pub | opaque
// @nico-fn: get | pub fn get(key: CacheKey) -> Option | effects=reads_clock | calls=time.clock::now
// @nico-fn: set | pub fn set(key: CacheKey, value: String, ttl: CacheTtl) -> () | effects=reads_clock | calls=time.clock::now
// @nico-fn: delete | pub fn delete(key: CacheKey) -> () | effects= | calls=

/// Opaque cache key. The inner `String` is not exposed to prevent callers
/// from constructing keys without going through a designated constructor.
#[allow(dead_code)]
pub struct CacheKey(String);

/// TTL duration in seconds.
#[allow(dead_code)]
pub struct CacheTtl(u64);

/// Look up a cache entry by key.
///
/// Checks whether the stored entry has expired via time.clock::now().
/// Returns `None` if the key is absent or the entry has expired.
pub fn get(_key: CacheKey) -> Option<String> {
    todo!()
}

/// Insert or overwrite a cache entry with the given TTL.
///
/// Records a timestamp from time.clock::now() alongside the value so
/// that `get` can compute when the entry expires.
pub fn set(_key: CacheKey, _value: String, _ttl: CacheTtl) {
    todo!()
}

/// Remove a cache entry unconditionally.
pub fn delete(_key: CacheKey) {
    todo!()
}
