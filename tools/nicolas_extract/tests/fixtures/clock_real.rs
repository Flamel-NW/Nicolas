// Golden fixture for nicolas-extract T2.1.1.
// Represents the `time.clock` boundary module body — the only module that
// may call system clock APIs under the Restricted Rust constraint.
use std::time::{SystemTime, UNIX_EPOCH};

pub struct Timestamp(u64);
pub struct Duration(u64);

pub fn now() -> Timestamp {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("time went backwards")
        .as_secs();
    Timestamp(secs)
}
