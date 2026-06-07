/// nicolas-extract — CLI entry point.
///
/// Usage: nicolas-extract <file.rs>
///
/// Prints a JSON object to stdout following the `nicolas-extract/0.1` schema.
/// Exit codes: 0 = success, 1 = usage / IO / parse error.
fn main() {
    let path = match std::env::args().nth(1) {
        Some(p) => p,
        None => {
            eprintln!("Usage: nicolas-extract <file.rs>");
            std::process::exit(1);
        }
    };

    let src = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("Error reading {path}: {e}");
            std::process::exit(1);
        }
    };

    let output = match nicolas_extract::extract(&src) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("Parse error in {path}: {e}");
            std::process::exit(1);
        }
    };

    println!(
        "{}",
        serde_json::to_string_pretty(&output).expect("serialization error")
    );
}
