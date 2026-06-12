// @nico-module: user.profile_service
// @nico-intent: 组合 cache.kv、user.store、audit.log 和 user.types，提供用户档案的主要公开 API。cache read-through：先查 cache，miss 时从 store 加载并回填；写入时同步更新 store，使 cache 失效。每次操作均通过 audit.log 记录 audit event。
// @nico-imports: user.types, cache.kv, user.store, audit.log
// @nico-module-effects: reads_clock, db.read, db.write, audit.write
// @nico-fn: get_profile | pub fn get_profile(id: UserId) -> Option | effects=reads_clock,db.read,audit.write | calls=cache.kv::get,user.store::load_profile,cache.kv::set,audit.log::new_event,audit.log::record
// @nico-fn: update_profile | pub fn update_profile(profile: UserProfile) -> () | effects=reads_clock,db.write,audit.write | calls=audit.log::new_event,user.store::save_profile,cache.kv::set,audit.log::record

use super::types::{UserId, UserProfile};
use super::store;
use crate::cache::kv;
use crate::cache::kv::{CacheKey, CacheTtl};
use crate::audit::log;
use crate::audit::log::{AuditActor, ProfileAuditAction};

/// Retrieve the user profile for the given ID.
///
/// Checks the cache first (via cache.kv::get). On a cache miss, loads the
/// profile from user.store::load_profile and backfills the cache. Records a
/// ProfileViewed audit event. Returns `None` if no profile exists.
pub fn get_profile(_id: UserId) -> Option<UserProfile> {
    let result = if kv::get(CacheKey(String::new())).is_some() {
        // skeleton: cache hit; real impl would deserialize cached string
        store::load_profile(_id)
    } else {
        let profile = store::load_profile(_id);
        // backfill cache on miss
        kv::set(CacheKey(String::new()), String::new(), CacheTtl(300));
        profile
    };
    let event = log::new_event(
        AuditActor::System,
        ProfileAuditAction::ProfileViewed,
        super::types::new_user_id(0),
    );
    log::record(event);
    result
}

/// Persist an updated user profile and refresh the cache.
///
/// Writes the profile via user.store::save_profile, updates the cache entry
/// via cache.kv::set, and records a ProfileUpdated audit event.
pub fn update_profile(_profile: UserProfile) {
    let key = CacheKey(String::new());
    let ttl = CacheTtl(300);
    let event = log::new_event(
        AuditActor::System,
        ProfileAuditAction::ProfileUpdated,
        super::types::new_user_id(0),
    );
    store::save_profile(_profile);
    kv::set(key, String::new(), ttl);
    log::record(event);
}
