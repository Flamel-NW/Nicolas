// @nico-module: user.profile_service
// @nico-intent: Orchestrates cache.kv, user.store, and user.types to provide the primary user profile API. Implements cache read-through: check cache first, load from store on miss and backfill. All clock reads are indirect via cache.kv.
// @nico-imports: user.types, cache.kv, user.store
// @nico-module-effects: reads_clock, db.read, db.write
// @nico-fn: get_profile | pub fn get_profile(id: UserId) -> Option | effects=reads_clock,db.read | calls=cache.kv::get,user.store::load_profile
// @nico-fn: update_profile | pub fn update_profile(profile: UserProfile) -> () | effects=reads_clock,db.read,db.write | calls=cache.kv::set,user.store::save_profile

use super::types::{UserId, UserProfile};
use super::store;
use crate::cache::kv;

/// Retrieve the user profile for the given ID.
///
/// Checks the cache first (via cache.kv::get). On a cache miss, loads
/// the profile from persistent storage via user.store::load_profile and
/// backfills the cache. Returns `None` if no profile exists.
pub fn get_profile(_id: UserId) -> Option<UserProfile> {
    todo!()
}

/// Persist an updated user profile and refresh the cache.
///
/// Writes the profile via user.store::save_profile, then updates the
/// cache entry via cache.kv::set so that subsequent get_profile calls
/// see the new data.
pub fn update_profile(_profile: UserProfile) {
    todo!()
}
