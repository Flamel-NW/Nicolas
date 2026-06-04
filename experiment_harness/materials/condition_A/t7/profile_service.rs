use super::types::{UserId, UserProfile};
use super::store;
use crate::cache::kv;

/// Retrieve the user profile for the given ID.
///
/// Checks the cache first. On a cache miss, loads the profile from
/// persistent storage and backfills the cache. Returns `None` if no
/// profile exists in either the cache or the store.
pub fn get_profile(_id: UserId) -> Option<UserProfile> {
    todo!()
}

/// Persist an updated user profile and refresh the cache.
///
/// Writes the profile to persistent storage, then updates or invalidates
/// the corresponding cache entry so that subsequent `get_profile` calls
/// see the new data.
pub fn update_profile(_profile: UserProfile) {
    todo!()
}
