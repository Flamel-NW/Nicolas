// @nico-module: user.types
// @nico-intent: Provides shared value types for user identity, contact, and lifecycle state. Pure data types only; no effects, no external dependencies.
// @nico-imports:
// @nico-module-effects:
// @nico-type: UserId | pub | opaque
// @nico-type: EmailAddress | pub | opaque
// @nico-type: UserStatus | pub | enum
// @nico-type: UserProfile | pub | struct
// @nico-fn: new_user_id | pub fn new_user_id(raw: u64) -> UserId | effects= | calls=
// @nico-fn: new_email | pub fn new_email(raw: String) -> Result | effects= | calls=
// @nico-fn: new_profile | pub fn new_profile(id: UserId, email: EmailAddress, status: UserStatus) -> UserProfile | effects= | calls=

/// Opaque user identifier. The inner `u64` is not exposed to prevent
/// callers from constructing arbitrary IDs.
#[allow(dead_code)]
pub struct UserId(u64);

/// A validated email address.
#[allow(dead_code)]
pub struct EmailAddress(String);

/// Closed set of user lifecycle states.
pub enum UserStatus {
    Active,
    Inactive,
    Suspended,
}

/// Aggregated user profile: identity + contact + lifecycle state.
pub struct UserProfile {
    pub id:     UserId,
    pub email:  EmailAddress,
    pub status: UserStatus,
}

/// Wrap a raw `u64` into a `UserId`.
pub fn new_user_id(raw: u64) -> UserId {
    UserId(raw)
}

/// Validate and wrap a raw string into an `EmailAddress`.
///
/// Returns `Err` if the string does not contain `@` or the domain part
/// is empty.
pub fn new_email(raw: String) -> Result<EmailAddress, String> {
    let parts: Vec<&str> = raw.splitn(2, '@').collect();
    if parts.len() != 2 || parts[0].is_empty() || parts[1].is_empty() {
        return Err(format!("invalid email address: {:?}", raw));
    }
    Ok(EmailAddress(raw))
}

/// Combine identity, contact, and status into a `UserProfile`.
pub fn new_profile(id: UserId, email: EmailAddress, status: UserStatus) -> UserProfile {
    UserProfile { id, email, status }
}
