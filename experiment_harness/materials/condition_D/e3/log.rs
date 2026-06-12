// @nico-module: audit.log
// @nico-intent: 记录关于用户档案操作的安全 audit events。audit.log 是整个场景中唯一的 audit 边界模块，集中承载 audit.write effect。上层模块通过 import audit.log 显式记录操作，不得散落式 logging。只接收不包含 secrets 和不必要 sensitive fields 的 event shapes。
// @nico-imports: user.types
// @nico-module-effects: audit.write
// @nico-type: AuditEvent | pub | struct
// @nico-type: AuditActor | pub | enum
// @nico-type: ProfileAuditAction | pub | enum
// @nico-fn: new_event | pub fn new_event(actor: AuditActor, action: ProfileAuditAction, subject_id: UserId) -> AuditEvent | effects= | calls=
// @nico-fn: record | pub fn record(event: AuditEvent) -> () | effects=audit.write | calls=

use crate::user::types::UserId;

/// Who performed the audited operation.
pub enum AuditActor {
    System,
    User(UserId),
}

/// The type of profile operation being audited.
pub enum ProfileAuditAction {
    ProfileViewed,
    ProfileUpdated,
    UserDeactivated,
}

/// An audit event containing actor, action type, and the subject user ID.
/// Does not carry secrets or sensitive profile payloads.
pub struct AuditEvent {
    pub actor:      AuditActor,
    pub action:     ProfileAuditAction,
    pub subject_id: UserId,
}

/// Construct an audit event from its components.
pub fn new_event(actor: AuditActor, action: ProfileAuditAction, subject_id: UserId) -> AuditEvent {
    AuditEvent { actor, action, subject_id }
}

/// Record an audit event to the audit log.
pub fn record(_event: AuditEvent) {
    todo!("audit.log::record: real implementation pending")
}
