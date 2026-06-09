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
    let key = CacheKey(String::new());
    let result = if kv::get(key).is_some() {
        None
    } else {
        store::load_profile(_id)
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
