// @nico-module: session.types
// @nico-intent: 提供 session 域的核心值类型。所有类型均为纯值类型：无 IO、无时钟、无持久化副作用。其他模块通过 import session.types 使用这些类型，而非直接构造原始数据。
// @nico-imports: user.types
// @nico-module-effects:
// @nico-type: SessionId | pub | opaque
// @nico-type: SessionToken | pub | opaque
// @nico-type: SessionStatus | pub | enum
// @nico-type: SessionInfo | pub | struct
// @nico-fn: new_session_id | pub fn new_session_id(raw: u64) -> SessionId | effects= | calls=
// @nico-fn: new_session_token | pub fn new_session_token(raw: String) -> SessionToken | effects= | calls=
// @nico-fn: new_session_info | pub fn new_session_info(id: SessionId, user_id: UserId, token: SessionToken, status: SessionStatus) -> SessionInfo | effects= | calls=

use crate::user::types::UserId;

#[allow(dead_code)]
pub struct SessionId(u64);

#[allow(dead_code)]
pub struct SessionToken(String);

pub enum SessionStatus {
    Active,
    Expired,
    Revoked,
}

pub struct SessionInfo {
    pub id:      SessionId,
    pub user_id: UserId,
    pub token:   SessionToken,
    pub status:  SessionStatus,
}

pub fn new_session_id(_raw: u64) -> SessionId {
    todo!()
}

pub fn new_session_token(_raw: String) -> SessionToken {
    todo!()
}

pub fn new_session_info(
    _id: SessionId,
    _user_id: UserId,
    _token: SessionToken,
    _status: SessionStatus,
) -> SessionInfo {
    todo!()
}
