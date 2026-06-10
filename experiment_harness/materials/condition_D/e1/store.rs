// @nico-module: user.store
// @nico-intent: Provides the user profile persistence boundary. This is the single module that directly accesses persistent storage for user data, centralising db.read and db.write effects.
// @nico-imports: user.types
// @nico-module-effects: db.read, db.write
// @nico-fn: load_profile | pub fn load_profile(id: UserId) -> Option | effects=db.read | calls=
// @nico-fn: save_profile | pub fn save_profile(profile: UserProfile) -> () | effects=db.write | calls=
// @nico-fn: mark_deactivated | pub fn mark_deactivated(id: UserId) -> () | effects=db.read,db.write | calls=

use super::types::{UserId, UserProfile};

/// Fetch the stored profile for the given user ID.
///
/// Returns `None` if no profile exists for `id`.
pub fn load_profile(_id: UserId) -> Option<UserProfile> {
    todo!()
}

/// Persist a user profile, inserting or overwriting as needed.
pub fn save_profile(_profile: UserProfile) {
    todo!()
}

/// Set the user's status to `Inactive` in the persistent store.
///
/// Reads the current profile, updates the status, and writes it back.
/// If `id` does not exist, this is a no-op.
pub fn mark_deactivated(_id: UserId) {
    todo!()
}
