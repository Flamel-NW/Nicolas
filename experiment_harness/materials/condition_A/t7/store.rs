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
