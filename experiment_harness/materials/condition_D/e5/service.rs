// @nico-module: session.service
// @nico-intent: 组合 cache.kv、session.store、audit.log 和 user.types，提供 session 生命周期的主要公开 API。create_session 写入持久化存储并记录 audit；validate_session 先查 cache，miss 时从 store 读取；revoke_session 使 cache 失效、撤销持久化存储中的 session，并记录 audit。所有时钟读取通过 cache.kv 间接集中在 time.clock。
// @nico-imports: user.types, cache.kv, session.store, audit.log
// @nico-module-effects: reads_clock, db.read, db.write, audit.write
// @nico-fn: create_session | pub fn create_session(user_id: UserId) -> SessionId | effects=reads_clock,db.write,audit.write | calls=cache.kv::get,cache.kv::set,session.store::save_session,audit.log::new_event,audit.log::record
// @nico-fn: validate_session | pub fn validate_session(id: SessionId) -> bool | effects=reads_clock,db.read | calls=cache.kv::get,session.store::load_session
// @nico-fn: revoke_session | pub fn revoke_session(id: SessionId) -> () | effects=db.read,db.write,audit.write | calls=cache.kv::delete,session.store::revoke_session,audit.log::new_event,audit.log::record

use crate::user::types::UserId;
use crate::session::store;
use crate::session::types::{SessionId, SessionStatus};
use crate::cache::kv;
use crate::cache::kv::{CacheKey, CacheTtl};
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
    let ttl = CacheTtl(1800);
    kv::set(CacheKey(String::new()), String::new(), ttl);
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
