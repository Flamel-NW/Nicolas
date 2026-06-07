//! Integration tests for nicolas-extract (T2.1.1).
//!
//! Tests load golden fixture sources via `include_str!`, run `extract()`,
//! and assert both semantic properties (presence checks) and exact JSON
//! equality against the corresponding `*.expected.json` files.
//!
//! "Order-insensitive" comparison: sorted arrays are compared element-by-element.
//! Within `calls`, order follows AST traversal (documented in expected JSON).

use nicolas_extract::{extract, CallEntry, Output};

// ── helpers ─────────────────────────────────────────────────────────────────

fn find_fn<'a>(output: &'a Output, name: &str) -> &'a nicolas_extract::FnEntry {
    output
        .functions
        .iter()
        .find(|f| f.name == name)
        .unwrap_or_else(|| panic!("function '{}' not found in output", name))
}

fn has_call(calls: &[CallEntry], path: &str, kind: &str) -> bool {
    calls.iter().any(|c| c.path == path && c.kind == kind)
}

fn has_path_call(calls: &[CallEntry], path: &str) -> bool {
    has_call(calls, path, "path")
}

fn has_macro_call(calls: &[CallEntry], path: &str) -> bool {
    has_call(calls, path, "macro")
}

/// Normalise output for exact JSON comparison:
/// - top-level arrays are already sorted by extract().
/// - calls within functions preserve AST-traversal order (documented in expected JSON).
fn output_to_json(output: &Output) -> serde_json::Value {
    serde_json::to_value(output).expect("serialization failed")
}

fn expected_json(path: &str) -> serde_json::Value {
    serde_json::from_str(path).expect("expected JSON parse failed")
}

// ── clock_real fixture ───────────────────────────────────────────────────────

const CLOCK_SRC: &str = include_str!("fixtures/clock_real.rs");
const CLOCK_EXPECTED: &str = include_str!("fixtures/clock_real.expected.json");

#[test]
fn clock_real_parses_without_error() {
    extract(CLOCK_SRC).expect("extract should not fail on clock_real.rs");
}

#[test]
fn clock_real_schema_version() {
    let out = extract(CLOCK_SRC).unwrap();
    assert_eq!(out.schema, "nicolas-extract/0.1");
}

#[test]
fn clock_real_uses_system_time_imports() {
    let out = extract(CLOCK_SRC).unwrap();
    // Both SystemTime and UNIX_EPOCH should be present, sorted by alias.
    assert!(
        out.uses.iter().any(|u| u.alias == "SystemTime" && u.path == "std::time::SystemTime"),
        "missing use SystemTime"
    );
    assert!(
        out.uses.iter().any(|u| u.alias == "UNIX_EPOCH" && u.path == "std::time::UNIX_EPOCH"),
        "missing use UNIX_EPOCH"
    );
    // Sorted: SystemTime (S) before UNIX_EPOCH (U).
    let idx_st = out.uses.iter().position(|u| u.alias == "SystemTime").unwrap();
    let idx_ue = out.uses.iter().position(|u| u.alias == "UNIX_EPOCH").unwrap();
    assert!(idx_st < idx_ue, "uses should be sorted by alias");
}

#[test]
fn clock_real_now_has_system_time_call() {
    let out = extract(CLOCK_SRC).unwrap();
    let now_fn = find_fn(&out, "now");
    assert_eq!(now_fn.visibility, "pub");
    // Must detect the `std::time::SystemTime::now()` path call (via alias).
    assert!(
        has_path_call(&now_fn.calls, "SystemTime::now"),
        "expected path call 'SystemTime::now' in now() calls: {:?}",
        now_fn.calls
    );
}

#[test]
fn clock_real_now_head_is_system_time() {
    let out = extract(CLOCK_SRC).unwrap();
    let now_fn = find_fn(&out, "now");
    let entry = now_fn
        .calls
        .iter()
        .find(|c| c.path == "SystemTime::now")
        .expect("SystemTime::now call not found");
    assert_eq!(entry.head, "SystemTime");
}

#[test]
fn clock_real_types_newtype() {
    let out = extract(CLOCK_SRC).unwrap();
    assert!(out.types.iter().any(|t| t.name == "Timestamp" && t.form == "newtype"), "Timestamp newtype");
    assert!(out.types.iter().any(|t| t.name == "Duration"  && t.form == "newtype"), "Duration newtype");
    // Sorted: Duration (D) before Timestamp (T).
    let idx_d = out.types.iter().position(|t| t.name == "Duration").unwrap();
    let idx_t = out.types.iter().position(|t| t.name == "Timestamp").unwrap();
    assert!(idx_d < idx_t, "types should be sorted by name");
}

/// Full exact comparison against clock_real.expected.json.
#[test]
fn clock_real_exact_json() {
    let out = extract(CLOCK_SRC).unwrap();
    let got = output_to_json(&out);
    let want = expected_json(CLOCK_EXPECTED);
    assert_eq!(got, want, "output does not match clock_real.expected.json");
}

// ── kv_real fixture ──────────────────────────────────────────────────────────

const KV_SRC: &str = include_str!("fixtures/kv_real.rs");
const KV_EXPECTED: &str = include_str!("fixtures/kv_real.expected.json");

#[test]
fn kv_real_parses_without_error() {
    extract(KV_SRC).expect("extract should not fail on kv_real.rs");
}

#[test]
fn kv_real_uses_clock_alias() {
    let out = extract(KV_SRC).unwrap();
    assert!(
        out.uses.iter().any(|u| u.alias == "clock" && u.path == "crate::time::clock" && !u.glob),
        "expected use clock → crate::time::clock"
    );
}

#[test]
fn kv_real_get_has_clock_now_path_call() {
    let out = extract(KV_SRC).unwrap();
    let get_fn = find_fn(&out, "get");
    assert_eq!(get_fn.visibility, "pub");
    assert!(
        has_path_call(&get_fn.calls, "clock::now"),
        "get() must have path call 'clock::now', got: {:?}",
        get_fn.calls
    );
    let entry = get_fn.calls.iter().find(|c| c.path == "clock::now").unwrap();
    assert_eq!(entry.head, "clock", "head of clock::now must be 'clock'");
}

#[test]
fn kv_real_set_has_clock_now_path_call() {
    let out = extract(KV_SRC).unwrap();
    let set_fn = find_fn(&out, "set");
    assert!(
        has_path_call(&set_fn.calls, "clock::now"),
        "set() must have path call 'clock::now', got: {:?}",
        set_fn.calls
    );
}

/// Key assertion from the plan: delete() has NO path calls (only macro).
#[test]
fn kv_real_delete_has_no_path_calls() {
    let out = extract(KV_SRC).unwrap();
    let delete_fn = find_fn(&out, "delete");
    let path_calls: Vec<_> = delete_fn.calls.iter().filter(|c| c.kind == "path").collect();
    assert!(
        path_calls.is_empty(),
        "delete() should have no path calls, found: {:?}",
        path_calls
    );
}

#[test]
fn kv_real_delete_has_todo_macro() {
    let out = extract(KV_SRC).unwrap();
    let delete_fn = find_fn(&out, "delete");
    assert!(
        has_macro_call(&delete_fn.calls, "todo"),
        "delete() should have macro call 'todo', got: {:?}",
        delete_fn.calls
    );
}

#[test]
fn kv_real_functions_sorted() {
    let out = extract(KV_SRC).unwrap();
    let names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
    assert_eq!(names, vec!["delete", "get", "set"], "functions must be sorted by name");
}

#[test]
fn kv_real_types_newtype() {
    let out = extract(KV_SRC).unwrap();
    assert!(out.types.iter().any(|t| t.name == "CacheKey" && t.form == "newtype"), "CacheKey newtype");
    assert!(out.types.iter().any(|t| t.name == "CacheTtl" && t.form == "newtype"), "CacheTtl newtype");
    let idx_k = out.types.iter().position(|t| t.name == "CacheKey").unwrap();
    let idx_t = out.types.iter().position(|t| t.name == "CacheTtl").unwrap();
    assert!(idx_k < idx_t, "CacheKey (K) must sort before CacheTtl (T)");
}

/// Full exact comparison against kv_real.expected.json.
#[test]
fn kv_real_exact_json() {
    let out = extract(KV_SRC).unwrap();
    let got = output_to_json(&out);
    let want = expected_json(KV_EXPECTED);
    assert_eq!(got, want, "output does not match kv_real.expected.json");
}
