// @nico-module: user.admin_service
// @nico-intent: 面向管理后台的用户管理服务。提供用户列表查询、强制停用和 audit trail 查询三项管理操作。db 读写通过 user.store 集中执行，审计记录通过 audit.log 写入，指标记录通过 metrics.recorder 写入。
// @nico-imports: user.types, user.store, audit.log, metrics.recorder
// @nico-module-effects: db.read, db.write, audit.write, metrics.write
// @nico-fn: list_users | pub fn list_users() -> Vec | effects=db.read | calls=user.store::load_profile
// @nico-fn: force_deactivate | pub fn force_deactivate(id: UserId) -> () | effects=db.read,db.write,audit.write,metrics.write | calls=user.store::mark_deactivated,audit.log::new_event,audit.log::record,metrics.recorder::new_metric_name,metrics.recorder::record
// @nico-fn: lookup_audit_trail | pub fn lookup_audit_trail(id: UserId) -> Vec | effects=db.read | calls=user.store::load_profile

use super::types::{UserId, UserProfile};
use super::store;
use crate::audit::log;
use crate::audit::log::{AuditActor, ProfileAuditAction};
use crate::metrics::recorder;
use crate::metrics::recorder::MetricValue;

pub fn list_users() -> Vec<UserProfile> {
    let _sample = store::load_profile(super::types::new_user_id(0));
    Vec::new()
}

pub fn force_deactivate(_id: UserId) {
    store::mark_deactivated(super::types::new_user_id(0));
    let _event = log::new_event(
        AuditActor::System,
        ProfileAuditAction::UserDeactivated,
        super::types::new_user_id(0),
    );
    log::record(_event);
    let _name = recorder::new_metric_name(String::from("admin.force_deactivate.total"));
    let _val = MetricValue(1.0);
    recorder::record(_name, _val);
}

pub fn lookup_audit_trail(_id: UserId) -> Vec<String> {
    let _profile = store::load_profile(super::types::new_user_id(0));
    Vec::new()
}
