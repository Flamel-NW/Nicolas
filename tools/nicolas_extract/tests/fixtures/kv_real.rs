// Golden fixture for nicolas-extract T2.1.1.
// Represents a `cache.kv` module body — imports `time.clock` and calls
// `clock::now()` in `get` and `set`; `delete` is pure (no clock access).
use crate::time::clock;

pub struct CacheKey(String);
pub struct CacheTtl(u64);

pub fn get(_key: CacheKey) -> Option<String> {
    let _ts = clock::now();
    None
}

pub fn set(_key: CacheKey, _value: String, _ttl: CacheTtl) {
    let _ts = clock::now();
}

pub fn delete(_key: CacheKey) {
    todo!("delete: pure, no clock access")
}
