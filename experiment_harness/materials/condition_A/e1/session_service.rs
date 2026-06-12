use crate::user::types::UserId;
use crate::session::store;
use crate::session::types::{SessionId, SessionStatus};
use crate::cache::kv;
use crate::cache::kv::CacheKey;
use crate::audit::log;
use crate::audit::log::{AuditActor, ProfileAuditAction};

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

pub fn validate_session(_id: SessionId) -> bool {
    let _cached = kv::get(CacheKey(String::new()));
    let _stored = store::load_session(crate::session::types::new_session_id(0));
    _stored.is_some()
}

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
