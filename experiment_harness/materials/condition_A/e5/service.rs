use crate::user::types::UserId;
use crate::session::store;
use crate::session::types::{SessionId, SessionStatus};
use crate::cache::kv;
use crate::cache::kv::CacheKey;
use crate::audit::log;
use crate::audit::log::{AuditActor, ProfileAuditAction};

/// Create a new session for the given user, persist it, and record an audit event.
///
/// Reads the clock indirectly via cache.kv::get (TTL check).
///
/// # Effects
/// - `reads_clock`: via cache.kv::get
/// - `db.write`: via session.store::save_session
/// - `audit.write`: via audit.log::record
pub fn create_session(_user_id: UserId) -> SessionId {
    let _cached = kv::get(CacheKey(String::new()));
    let _token = crate::session::types::new_session_token(String::new());
    let _info = crate::session::types::new_session_info(
        crate::session::types::new_session_id(0),
        crate::user::types::new_user_id(0),
        _token,
        SessionStatus::Active,
    );
    store::save_session(_info);
    let _event = log::new_event(
        AuditActor::System,
        ProfileAuditAction::ProfileViewed,
        crate::user::types::new_user_id(0),
    );
    log::record(_event);
    crate::session::types::new_session_id(0)
}

/// Check whether a session is still valid.
///
/// Queries the cache first; falls back to persistent store on a miss.
///
/// # Effects
/// - `reads_clock`: via cache.kv::get
/// - `db.read`: via session.store::load_session
pub fn validate_session(_id: SessionId) -> bool {
    let _cached = kv::get(CacheKey(String::new()));
    let _stored = store::load_session(crate::session::types::new_session_id(0));
    _stored.is_some()
}

/// Revoke an existing session, invalidate its cache entry, and record an audit event.
///
/// # Effects
/// - `db.read`: via session.store::revoke_session (reads before delete)
/// - `db.write`: via session.store::revoke_session
/// - `audit.write`: via audit.log::record
pub fn revoke_session(_id: SessionId) {
    kv::delete(CacheKey(String::new()));
    store::revoke_session(crate::session::types::new_session_id(0));
    let _event = log::new_event(
        AuditActor::System,
        ProfileAuditAction::UserDeactivated,
        crate::user::types::new_user_id(0),
    );
    log::record(_event);
}
