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
