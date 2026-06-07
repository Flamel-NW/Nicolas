//! nicolas-extract — syntax-level Rust AST extractor (T2.1.1).
//!
//! Parses a `.rs` source file and extracts:
//! - `uses`      : top-level `use` declarations (alias → full path)
//! - `functions` : top-level `fn` items with their call sites
//! - `types`     : top-level `pub struct` / `pub enum` / `pub type` items
//!
//! **Design boundary (v0)**:
//! This is a *syntactic* extractor. It does not perform name resolution,
//! type inference, or cross-crate analysis. Under the Restricted Rust
//! constraint (all cross-module side-effects go through explicit Nicolas
//! module paths), syntactic extraction is sufficient and fully deterministic.
//! Method-call receiver types are not resolved; results must be labelled
//! accordingly (same caveat as condition-D "non-complete rustc extractor").
//!
//! Output schema: `nicolas-extract/0.1`.

use serde::{Deserialize, Serialize};
use syn::{
    visit::{self, Visit},
    Expr, ExprCall, ExprMethodCall, Fields, File, ItemEnum, ItemFn, ItemStruct,
    ItemType, ItemUse, UseTree, Visibility,
};

// ─── Output schema ─────────────────────────────────────────────────────────

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct Output {
    pub schema: String,
    pub uses: Vec<UseEntry>,
    pub functions: Vec<FnEntry>,
    pub types: Vec<TypeEntry>,
}

/// A single `use` import after flattening (including grouped / renamed / glob forms).
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct UseEntry {
    /// Last path segment (or rename target), used as the local alias.
    pub alias: String,
    /// Full import path as written in source (before alias rename, if any).
    pub path: String,
    /// `true` for `use foo::*` glob imports.
    pub glob: bool,
}

/// A top-level function item.
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct FnEntry {
    pub name: String,
    /// `"pub"` or `"private"`. `pub(crate)` etc. are `"private"` in this schema.
    pub visibility: String,
    /// All call sites found in the function body, in source traversal order.
    pub calls: Vec<CallEntry>,
}

/// A single call site within a function body.
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct CallEntry {
    /// Stringified callee path (ident segments joined with `::`).
    /// For method calls, this is just the method name.
    /// For macro calls, this is the macro path (e.g. `"todo"`).
    pub path: String,
    /// First segment of `path`. Allows O(1) categorisation in Python:
    /// `"std"` / `"core"` → system API; known alias → Nicolas module call.
    pub head: String,
    /// `"path"` | `"method"` | `"macro"`.
    pub kind: String,
}

/// A top-level type item.
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct TypeEntry {
    pub name: String,
    /// `"newtype"` (single-field tuple struct), `"tuple_struct"`, `"struct"`,
    /// `"unit"`, `"enum"`, `"alias"`.
    pub form: String,
}

// ─── Public API ────────────────────────────────────────────────────────────

/// Parse `src` as a Rust source file and extract structural facts.
///
/// Returns `Err` only on syntax errors; missing or unusual constructs are
/// silently skipped (they are not relevant to Nicolas module extraction).
pub fn extract(src: &str) -> Result<Output, syn::Error> {
    let file: File = syn::parse_file(src)?;

    let mut visitor = FileVisitor {
        uses: Vec::new(),
        functions: Vec::new(),
        types: Vec::new(),
    };
    visitor.visit_file(&file);

    // Deterministic sort: uses by alias, functions by name, types by name.
    visitor.uses.sort_by(|a, b| a.alias.cmp(&b.alias));
    visitor.functions.sort_by(|a, b| a.name.cmp(&b.name));
    visitor.types.sort_by(|a, b| a.name.cmp(&b.name));

    Ok(Output {
        schema: "nicolas-extract/0.1".to_string(),
        uses: visitor.uses,
        functions: visitor.functions,
        types: visitor.types,
    })
}

// ─── File-level visitor ────────────────────────────────────────────────────

struct FileVisitor {
    uses: Vec<UseEntry>,
    functions: Vec<FnEntry>,
    types: Vec<TypeEntry>,
}

impl<'ast> Visit<'ast> for FileVisitor {
    // ── use declarations ───────────────────────────────────────────────────

    fn visit_item_use(&mut self, node: &'ast ItemUse) {
        let entries = flatten_use_tree("", &node.tree);
        self.uses.extend(entries);
        // No recursive visit: use items contain no nested items of interest.
    }

    // ── function definitions ───────────────────────────────────────────────

    fn visit_item_fn(&mut self, node: &'ast ItemFn) {
        let name = node.sig.ident.to_string();
        let visibility = match &node.vis {
            Visibility::Public(_) => "pub",
            _ => "private",
        }
        .to_string();

        // Collect all call sites in the function body via a nested visitor.
        let mut collector = CallCollector { calls: Vec::new() };
        collector.visit_block(&node.block);

        self.functions.push(FnEntry {
            name,
            visibility,
            calls: collector.calls,
        });

        // Do NOT call `visit::visit_item_fn(self, node)` here.
        // That would recurse into nested ItemFn items (if any) and add them
        // as additional top-level functions in our output, which is wrong.
    }

    // ── type definitions ───────────────────────────────────────────────────

    fn visit_item_struct(&mut self, node: &'ast ItemStruct) {
        if !matches!(node.vis, Visibility::Public(_)) {
            return;
        }
        let name = node.ident.to_string();
        let form = match &node.fields {
            Fields::Unnamed(f) if f.unnamed.len() == 1 => "newtype",
            Fields::Unnamed(_) => "tuple_struct",
            Fields::Named(_) => "struct",
            Fields::Unit => "unit",
        }
        .to_string();
        self.types.push(TypeEntry { name, form });
    }

    fn visit_item_enum(&mut self, node: &'ast ItemEnum) {
        if !matches!(node.vis, Visibility::Public(_)) {
            return;
        }
        self.types.push(TypeEntry {
            name: node.ident.to_string(),
            form: "enum".to_string(),
        });
    }

    fn visit_item_type(&mut self, node: &'ast ItemType) {
        if !matches!(node.vis, Visibility::Public(_)) {
            return;
        }
        self.types.push(TypeEntry {
            name: node.ident.to_string(),
            form: "alias".to_string(),
        });
    }
}

// ─── Call-site collector ───────────────────────────────────────────────────

struct CallCollector {
    calls: Vec<CallEntry>,
}

impl<'ast> Visit<'ast> for CallCollector {
    /// Path / free-function calls: `foo::bar(...)`, `SystemTime::now()`, etc.
    fn visit_expr_call(&mut self, node: &'ast ExprCall) {
        if let Expr::Path(expr_path) = node.func.as_ref() {
            let path = path_to_string(&expr_path.path);
            let head = expr_path
                .path
                .segments
                .first()
                .map(|s| s.ident.to_string())
                .unwrap_or_default();
            self.calls.push(CallEntry {
                path,
                head,
                kind: "path".to_string(),
            });
        }
        // Recurse into func expression and arguments to capture nested calls.
        visit::visit_expr_call(self, node);
    }

    /// Method calls: `receiver.method(...)`.
    /// Receiver type is not resolved (syntactic only). Recurse into receiver
    /// and args to capture the full call chain.
    fn visit_expr_method_call(&mut self, node: &'ast ExprMethodCall) {
        let method = node.method.to_string();
        self.calls.push(CallEntry {
            path: method.clone(),
            head: method,
            kind: "method".to_string(),
        });
        visit::visit_expr_method_call(self, node);
    }

    /// Catch ALL macro invocations via the common `syn::Macro` node.
    ///
    /// This hook is called from both `visit_expr_macro` (expression-level)
    /// and `visit_item_macro` (item-level, e.g. `todo!(...);` parsed as a
    /// statement-level item macro in some syn versions). Using `visit_macro`
    /// ensures coverage in both cases without needing two separate overrides.
    ///
    /// Macro token streams are opaque — do not recurse.
    fn visit_macro(&mut self, node: &'ast syn::Macro) {
        let path = path_to_string(&node.path);
        let head = node
            .path
            .segments
            .first()
            .map(|s| s.ident.to_string())
            .unwrap_or_default();
        self.calls.push(CallEntry {
            path,
            head,
            kind: "macro".to_string(),
        });
        // No recursion: macro token streams are opaque to syn.
    }

    /// Prevent nested `fn` definitions from having their calls attributed
    /// to the enclosing function.
    fn visit_item_fn(&mut self, _node: &'ast ItemFn) {
        // Intentionally empty: skip nested function items.
    }
}

// ─── Helpers ───────────────────────────────────────────────────────────────

/// Join a path prefix with a new segment using `::`.
fn join_path(prefix: &str, segment: &str) -> String {
    if prefix.is_empty() {
        segment.to_string()
    } else {
        format!("{}::{}", prefix, segment)
    }
}

/// Recursively flatten a `UseTree` into a list of `UseEntry` records.
///
/// `prefix` accumulates the path components encountered so far.
fn flatten_use_tree(prefix: &str, tree: &UseTree) -> Vec<UseEntry> {
    match tree {
        UseTree::Path(p) => {
            let new_prefix = join_path(prefix, &p.ident.to_string());
            flatten_use_tree(&new_prefix, &p.tree)
        }
        UseTree::Name(n) => {
            let ident = n.ident.to_string();
            let path = join_path(prefix, &ident);
            vec![UseEntry {
                alias: ident,
                path,
                glob: false,
            }]
        }
        UseTree::Rename(r) => {
            let ident = r.ident.to_string();
            let path = join_path(prefix, &ident);
            let alias = r.rename.to_string();
            vec![UseEntry {
                alias,
                path,
                glob: false,
            }]
        }
        UseTree::Glob(_) => {
            vec![UseEntry {
                alias: "*".to_string(),
                path: prefix.to_string(),
                glob: true,
            }]
        }
        UseTree::Group(g) => g
            .items
            .iter()
            .flat_map(|item| flatten_use_tree(prefix, item))
            .collect(),
    }
}

/// Convert a `syn::Path` to a `::` separated string of ident segments.
/// Generic arguments (e.g. `Vec::<T>`) are omitted — only idents are kept.
fn path_to_string(path: &syn::Path) -> String {
    path.segments
        .iter()
        .map(|s| s.ident.to_string())
        .collect::<Vec<_>>()
        .join("::")
}

// ─── Unit tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod unit_tests {
    use super::*;

    #[test]
    fn join_path_empty_prefix() {
        assert_eq!(join_path("", "foo"), "foo");
    }

    #[test]
    fn join_path_non_empty_prefix() {
        assert_eq!(join_path("crate::time", "clock"), "crate::time::clock");
    }

    #[test]
    fn flatten_simple_use() {
        let src = "use crate::time::clock;";
        let file: File = syn::parse_file(src).unwrap();
        let mut vis = FileVisitor {
            uses: Vec::new(),
            functions: Vec::new(),
            types: Vec::new(),
        };
        vis.visit_file(&file);
        assert_eq!(vis.uses.len(), 1);
        assert_eq!(vis.uses[0].alias, "clock");
        assert_eq!(vis.uses[0].path, "crate::time::clock");
        assert!(!vis.uses[0].glob);
    }

    #[test]
    fn flatten_grouped_use() {
        let src = "use std::time::{SystemTime, UNIX_EPOCH};";
        let file: File = syn::parse_file(src).unwrap();
        let mut vis = FileVisitor {
            uses: Vec::new(),
            functions: Vec::new(),
            types: Vec::new(),
        };
        vis.visit_file(&file);
        assert_eq!(vis.uses.len(), 2);
        let aliases: Vec<&str> = vis.uses.iter().map(|u| u.alias.as_str()).collect();
        assert!(aliases.contains(&"SystemTime"));
        assert!(aliases.contains(&"UNIX_EPOCH"));
    }

    #[test]
    fn newtype_struct_form() {
        let src = "pub struct Foo(u64);";
        let output = extract(src).unwrap();
        assert_eq!(output.types.len(), 1);
        assert_eq!(output.types[0].form, "newtype");
    }

    #[test]
    fn named_struct_form() {
        let src = "pub struct Foo { x: u32 }";
        let output = extract(src).unwrap();
        assert_eq!(output.types[0].form, "struct");
    }

    #[test]
    fn private_struct_excluded() {
        let src = "struct Foo(u64);";
        let output = extract(src).unwrap();
        assert!(output.types.is_empty());
    }

    #[test]
    fn path_call_captured() {
        let src = r#"
            pub fn f() {
                foo::bar();
            }
        "#;
        let output = extract(src).unwrap();
        let f = &output.functions[0];
        assert!(f.calls.iter().any(|c| c.path == "foo::bar" && c.kind == "path" && c.head == "foo"));
    }

    #[test]
    fn method_call_captured() {
        let src = r#"
            pub fn f() -> u64 {
                let x: u64 = 42;
                x.count_ones() as u64
            }
        "#;
        let output = extract(src).unwrap();
        let f = &output.functions[0];
        assert!(f.calls.iter().any(|c| c.kind == "method" && c.path == "count_ones"));
    }

    #[test]
    fn macro_call_captured() {
        let src = r#"
            pub fn f() {
                todo!("not yet");
            }
        "#;
        let output = extract(src).unwrap();
        let f = &output.functions[0];
        assert!(f.calls.iter().any(|c| c.kind == "macro" && c.path == "todo"));
    }

    #[test]
    fn functions_sorted_by_name() {
        let src = r#"
            pub fn z() {}
            pub fn a() {}
        "#;
        let output = extract(src).unwrap();
        assert_eq!(output.functions[0].name, "a");
        assert_eq!(output.functions[1].name, "z");
    }
}
