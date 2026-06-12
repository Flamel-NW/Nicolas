// @nico-module: session.store
// @nico-intent: 提供 session 数据的持久化读写边界。session.store 是整个场景中唯一直接操作持久化存储的 session 模块，集中承载 db.read 和 db.write effects。上层模块通过 import session.store 使用持久化操作，不得绕过此边界直接访问存储层。
// @nico-imports: session.types
// @nico-module-effects: db.read, db.write
// @nico-fn: load_session | pub fn load_session(id: SessionId) -> Option | effects=db.read | calls=
// @nico-fn: save_session | pub fn save_session(info: SessionInfo) -> () | effects=db.write | calls=
// @nico-fn: revoke_session | pub fn revoke_session(id: SessionId) -> () | effects=db.read,db.write | calls=

use super::types::{SessionId, SessionInfo};

pub fn load_session(_id: SessionId) -> Option<SessionInfo> {
    todo!()
}

pub fn save_session(_info: SessionInfo) {
    todo!()
}

pub fn revoke_session(_id: SessionId) {
    todo!()
}
