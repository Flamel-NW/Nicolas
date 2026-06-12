use crate::user::types::UserId;

pub struct SessionId(u64);

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

pub fn new_session_id(raw: u64) -> SessionId {
    SessionId(raw)
}

pub fn new_session_token(raw: String) -> SessionToken {
    SessionToken(raw)
}

pub fn new_session_info(
    id: SessionId,
    user_id: UserId,
    token: SessionToken,
    status: SessionStatus,
) -> SessionInfo {
    SessionInfo { id, user_id, token, status }
}
