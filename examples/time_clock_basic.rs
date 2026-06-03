//! `basic_now` — minimal usage example for `time.clock`.
//!
//! Corresponds to the `example basic_now { ... }` block in the
//! `time.clock` `.nico` spec and the `examples` entry in
//! `time.clock.semantic.json`.
//!
//! Run with:
//!   cargo run --example time_clock_basic
//!
//! # What this example validates
//! - `time::clock::now()` compiles and returns a `Timestamp`.
//! - The `Timestamp` type is opaque (cannot be inspected without
//!   an explicit conversion helper).
//!
//! NOTE: Because `now()` is currently `todo!()`, running this example
//! will panic at runtime. The compile-time check (type correctness) is
//! the meaningful validation at this prototype stage.

fn main() {
    // In the real implementation this would return the current wall-clock
    // time. At prototype stage it panics, which is expected.
    let _t: nicolas::time::clock::Timestamp = nicolas::time::clock::now();
    println!("time.clock::now() returned a Timestamp.");
}
