"""
High-level Semantic DB query helpers for condition C experiments.

All outputs are mechanical summaries of trusted tables. These helpers do not
read soft semantic content and do not infer facts beyond graph traversal.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


VALID_QUERIES = {
    "module_surface",
    "module_dependents",
    "type_dependents",
    "function_callers",
    "effect_chain",
    "affected_modules",
}


class SemanticQueryError(ValueError):
    """Raised when a semantic query request is invalid."""


def run_semantic_query(params: dict[str, Any], trusted_path: Path) -> str:
    query = str(params.get("query", "")).strip()
    if query not in VALID_QUERIES:
        allowed = ", ".join(sorted(VALID_QUERIES))
        return f"Error: unknown semantic query '{query}'. Expected one of: {allowed}"
    if not trusted_path.exists():
        return f"Error: sem_trusted.db not found: {trusted_path}"

    try:
        with _connect(trusted_path) as conn:
            if query == "module_surface":
                return _module_surface(conn, _required(params, "module"))
            if query == "module_dependents":
                return _module_dependents(
                    conn,
                    _required(params, "module"),
                    transitive=_as_bool(params.get("transitive"), default=True),
                )
            if query == "type_dependents":
                return _type_dependents(
                    conn,
                    _required(params, "type_name"),
                    module=_optional(params, "module"),
                    transitive=_as_bool(params.get("transitive"), default=True),
                )
            if query == "function_callers":
                return _function_callers(
                    conn,
                    _required(params, "module"),
                    _required(params, "function"),
                    transitive=_as_bool(params.get("transitive"), default=False),
                )
            if query == "effect_chain":
                return _effect_chain(
                    conn,
                    _required(params, "module"),
                    function=_optional(params, "function"),
                    effect=_optional(params, "effect"),
                )
            if query == "affected_modules":
                return _affected_modules(
                    conn,
                    _required(params, "module"),
                    type_name=_optional(params, "type_name"),
                    function=_optional(params, "function"),
                    effect=_optional(params, "effect"),
                    transitive=_as_bool(params.get("transitive"), default=True),
                )
    except (SemanticQueryError, sqlite3.Error) as e:
        return f"Error: {e}"

    return f"Error: unhandled semantic query '{query}'"


def _connect(trusted_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(trusted_path))
    conn.row_factory = sqlite3.Row
    return conn


def _required(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise SemanticQueryError(f"missing required parameter '{key}'")
    return value


def _optional(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _module_exists(conn: sqlite3.Connection, module: str) -> bool:
    return conn.execute("SELECT 1 FROM modules WHERE name=?", (module,)).fetchone() is not None


def _function_exists(conn: sqlite3.Connection, module: str, function: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM functions WHERE module_name=? AND name=?",
        (module, function),
    ).fetchone() is not None


def _module_candidates(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("SELECT name FROM modules ORDER BY name")]


def _ensure_module(conn: sqlite3.Connection, module: str) -> None:
    if not _module_exists(conn, module):
        raise SemanticQueryError(
            f"module '{module}' not found. Available modules: {_module_candidates(conn)}"
        )


def _module_surface(conn: sqlite3.Connection, module: str) -> str:
    _ensure_module(conn, module)
    module_row = conn.execute(
        "SELECT name, source, schema_version FROM modules WHERE name=?",
        (module,),
    ).fetchone()
    imports = _column(conn, "SELECT imported_module FROM imports WHERE module_name=? ORDER BY imported_module", module)
    types = conn.execute(
        "SELECT name, visibility, repr FROM types WHERE module_name=? ORDER BY name",
        (module,),
    ).fetchall()
    functions = conn.execute(
        "SELECT name, signature, visibility FROM functions WHERE module_name=? ORDER BY name",
        (module,),
    ).fetchall()
    function_effect_rows = conn.execute(
        "SELECT function_name, effect FROM effects "
        "WHERE module_name=? AND scope='function' ORDER BY function_name, effect",
        (module,),
    ).fetchall()
    function_effects = _group_rows(function_effect_rows, "function_name", "effect")
    module_effects = _column(
        conn,
        "SELECT effect FROM effects WHERE module_name=? AND scope='module' ORDER BY effect",
        module,
    )
    propagated = conn.execute(
        "SELECT effect, source_module, depth FROM propagated_effects "
        "WHERE module_name=? ORDER BY depth, effect, source_module",
        (module,),
    ).fetchall()
    examples = conn.execute(
        "SELECT example_id, path FROM examples WHERE module_name=? ORDER BY example_id",
        (module,),
    ).fetchall()

    lines = [
        f"module: {module_row['name']}",
        f"source: {module_row['source']}",
        f"schema_version: {module_row['schema_version']}",
        f"imports: {_compact_list(imports)}",
        "types:",
    ]
    lines.extend(
        f"- {row['name']} ({_compact_list([row['visibility'], row['repr']])})"
        for row in types
    )
    if not types:
        lines.append("- none")

    lines.append("functions:")
    for row in functions:
        effects = function_effects.get(row["name"], [])
        lines.append(f"- {row['signature']} | effects={_compact_list(effects)}")
    if not functions:
        lines.append("- none")

    lines.append(f"module_effects: {_compact_list(module_effects)}")
    lines.append("propagated_effects:")
    lines.extend(
        f"- {row['effect']} <- {row['source_module']} depth={row['depth']}"
        for row in propagated
    )
    if not propagated:
        lines.append("- none")

    lines.append("examples:")
    lines.extend(f"- {row['example_id']} -> {row['path']}" for row in examples)
    if not examples:
        lines.append("- none")
    return "\n".join(lines)


def _module_dependents(conn: sqlite3.Connection, module: str, transitive: bool) -> str:
    _ensure_module(conn, module)
    rows = _reverse_import_paths(conn, module, transitive=transitive)
    lines = [
        f"module_dependents: {module}",
        f"transitive: {str(transitive).lower()}",
    ]
    if not rows:
        lines.append("dependents: none")
        return "\n".join(lines)
    lines.append("dependents:")
    lines.extend(
        f"- {row['module']} depth={row['depth']} path={' -> '.join(row['path'])}"
        for row in rows
    )
    return "\n".join(lines)


def _type_dependents(
    conn: sqlite3.Connection,
    type_name: str,
    module: str | None,
    transitive: bool,
) -> str:
    if module:
        rows = conn.execute(
            "SELECT module_name, name FROM types WHERE module_name=? AND name=? ORDER BY module_name",
            (module, type_name),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT module_name, name FROM types WHERE name=? ORDER BY module_name",
            (type_name,),
        ).fetchall()
    if not rows:
        return f"Error: type '{type_name}' not found"
    if len(rows) > 1:
        providers = [row["module_name"] for row in rows]
        return f"Error: type '{type_name}' is ambiguous. Provide module. Candidates: {providers}"

    provider = rows[0]["module_name"]
    dependent_rows = _reverse_import_paths(conn, provider, transitive=transitive)
    lines = [
        f"type: {provider}.{type_name}",
        f"transitive: {str(transitive).lower()}",
        "dependents:",
    ]
    if dependent_rows:
        lines.extend(
            f"- {row['module']} depth={row['depth']} path={' -> '.join(row['path'])}"
            for row in dependent_rows
        )
    else:
        lines.append("- none")
    return "\n".join(lines)


def _function_callers(conn: sqlite3.Connection, module: str, function: str, transitive: bool) -> str:
    _ensure_module(conn, module)
    if not _function_exists(conn, module, function):
        return f"Error: function '{module}.{function}' not found"

    paths = _reverse_call_paths(conn, module, function, transitive=transitive)
    lines = [
        f"function_callers: {module}.{function}",
        f"transitive: {str(transitive).lower()}",
    ]
    if not paths:
        lines.append("callers: none")
        return "\n".join(lines)
    lines.append("callers:")
    lines.extend(
        f"- {row['caller']} depth={row['depth']} path={' -> '.join(row['path'])}"
        for row in paths
    )
    return "\n".join(lines)


def _effect_chain(
    conn: sqlite3.Connection,
    module: str,
    function: str | None,
    effect: str | None,
) -> str:
    _ensure_module(conn, module)
    if function and not _function_exists(conn, module, function):
        return f"Error: function '{module}.{function}' not found"

    lines = [
        "effect_chain:",
        f"module: {module}",
    ]
    if function:
        lines.append(f"function: {function}")
    if effect:
        lines.append(f"effect_filter: {effect}")

    module_effects = _filtered_effects(
        conn,
        "SELECT effect FROM effects WHERE module_name=? AND scope='module' ORDER BY effect",
        (module,),
        effect,
    )
    propagated = conn.execute(
        "SELECT effect, source_module, depth FROM propagated_effects "
        "WHERE module_name=? ORDER BY depth, effect, source_module",
        (module,),
    ).fetchall()
    if effect:
        propagated = [row for row in propagated if row["effect"] == effect]

    lines.append(f"module_effects: {_compact_list(module_effects)}")
    lines.append("propagated_effects:")
    if propagated:
        lines.extend(
            f"- {row['effect']} <- {row['source_module']} depth={row['depth']}"
            for row in propagated
        )
    else:
        lines.append("- none")

    if function:
        direct_effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND function_name=? ORDER BY effect",
            (module, function),
            effect,
        )
        lines.append(f"direct_function_effects: {_compact_list(direct_effects)}")
        call_rows = _forward_call_paths(conn, module, function, effect)
        lines.append("call_paths:")
        if call_rows:
            lines.extend(
                f"- {' -> '.join(row['path'])} | callee_effects={_compact_list(row['effects'])}"
                for row in call_rows
            )
        else:
            lines.append("- none")
    else:
        fn_rows = conn.execute(
            "SELECT name FROM functions WHERE module_name=? ORDER BY name",
            (module,),
        ).fetchall()
        lines.append("function_effects:")
        found = False
        for row in fn_rows:
            effects = _filtered_effects(
                conn,
                "SELECT effect FROM effects WHERE module_name=? AND function_name=? ORDER BY effect",
                (module, row["name"]),
                effect,
            )
            if effect and not effects:
                continue
            lines.append(f"- {module}.{row['name']}: {_compact_list(effects)}")
            found = True
        if not found:
            lines.append("- none")
    return "\n".join(lines)


def _affected_modules(
    conn: sqlite3.Connection,
    module: str,
    type_name: str | None,
    function: str | None,
    effect: str | None,
    transitive: bool,
) -> str:
    _ensure_module(conn, module)
    if function and not _function_exists(conn, module, function):
        return f"Error: function '{module}.{function}' not found"

    provider = module
    type_provider_line = "none"
    if type_name:
        rows = conn.execute(
            "SELECT module_name, name FROM types WHERE module_name=? AND name=?",
            (module, type_name),
        ).fetchall()
        if not rows:
            candidates = [
                row["module_name"]
                for row in conn.execute(
                    "SELECT module_name FROM types WHERE name=? ORDER BY module_name",
                    (type_name,),
                ).fetchall()
            ]
            if candidates:
                return (
                    f"Error: type '{type_name}' not found in module '{module}'. "
                    f"Candidates in other modules: {candidates}"
                )
            return f"Error: type '{type_name}' not found in module '{module}'"
        provider = rows[0]["module_name"]
        type_provider_line = f"{provider}.{type_name}"

    source_map = _module_source_map(conn)
    direct_importers = _reverse_import_paths(conn, provider, transitive=False)
    dependent_rows = _reverse_import_paths(conn, provider, transitive=transitive)
    candidate_modules = [provider] + [row["module"] for row in dependent_rows]
    candidate_reasons = {provider: "changed_or_provider_module"}
    for row in dependent_rows:
        candidate_reasons[row["module"]] = f"dependent_via_import_path:{' -> '.join(row['path'])}"

    lines = [
        "affected_modules:",
        f"source_module: {module}",
        f"type_filter: {type_name or 'none'}",
        f"function_filter: {function or 'none'}",
        f"effect_filter: {effect or 'none'}",
        f"transitive: {str(transitive).lower()}",
        f"direct_type_provider: {type_provider_line}",
        "direct_importers:",
    ]
    if direct_importers:
        lines.extend(
            f"- {row['module']} source={source_map.get(row['module'], 'unknown')}"
            for row in direct_importers
        )
    else:
        lines.append("- none")

    lines.append("candidate_check_modules:")
    lines.append(f"- {provider} depth=0 source={source_map.get(provider, 'unknown')} reason=changed_or_provider_module")
    for row in dependent_rows:
        lines.append(
            f"- {row['module']} depth={row['depth']} "
            f"source={source_map.get(row['module'], 'unknown')} path={' -> '.join(row['path'])}"
        )

    module_effects = _candidate_module_effects(conn, candidate_modules, effect)
    lines.append("matching_module_effects:")
    if module_effects:
        lines.extend(f"- {module}: {_compact_list(effects)}" for module, effects in module_effects)
    else:
        lines.append("- none")

    propagated = _candidate_propagated_effects(conn, candidate_modules, effect)
    lines.append("matching_propagated_effects:")
    if propagated:
        lines.extend(
            f"- {row['module_name']}: {row['effect']} <- {row['source_module']} depth={row['depth']}"
            for row in propagated
        )
    else:
        lines.append("- none")

    lines.append("effect_update_candidates:")
    if effect:
        lines.extend(_effect_update_candidate_lines(conn, candidate_modules, effect, source_map))
    else:
        lines.append("- none (effect_filter required)")

    lines.append("source_effect_update_plan:")
    if effect:
        lines.extend(_source_effect_update_plan_lines(
            conn,
            candidate_modules,
            effect,
            source_map,
            candidate_reasons,
        ))
    else:
        lines.append("- none (effect_filter required)")

    lines.append("source_edit_plan:")
    lines.extend(_source_edit_plan_lines(
        conn,
        module=module,
        provider=provider,
        dependent_rows=dependent_rows,
        candidate_modules=candidate_modules,
        type_name=type_name,
        function=function,
        effect=effect,
        source_map=source_map,
        candidate_reasons=candidate_reasons,
    ))
    return "\n".join(lines)


def _module_source_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["name"]: row["source"] or "unknown"
        for row in conn.execute("SELECT name, source FROM modules ORDER BY name")
    }


def _candidate_module_effects(
    conn: sqlite3.Connection,
    modules: list[str],
    effect_filter: str | None,
) -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    for module in modules:
        effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND scope='module' ORDER BY effect",
            (module,),
            effect_filter,
        )
        if effects:
            rows.append((module, effects))
    return rows


def _candidate_propagated_effects(
    conn: sqlite3.Connection,
    modules: list[str],
    effect_filter: str | None,
) -> list[sqlite3.Row]:
    module_set = set(modules)
    rows = conn.execute(
        "SELECT module_name, effect, source_module, depth FROM propagated_effects "
        "ORDER BY module_name, effect, source_module, depth"
    ).fetchall()
    return [
        row for row in rows
        if row["module_name"] in module_set and (effect_filter is None or row["effect"] == effect_filter)
    ]


def _effect_update_candidate_lines(
    conn: sqlite3.Connection,
    modules: list[str],
    effect: str,
    source_map: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    for module in modules:
        current_effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND scope='module' ORDER BY effect",
            (module,),
            None,
        )
        has_effect = effect in current_effects
        action = "verify_no_change" if has_effect else "add_effect"
        lines.append(
            f"- {module} action={action} has_effect={str(has_effect).lower()} "
            f"current_module_effects={_compact_list(current_effects)} "
            f"source={source_map.get(module, 'unknown')}"
        )
    return lines or ["- none"]


def _source_effect_update_plan_lines(
    conn: sqlite3.Connection,
    modules: list[str],
    effect: str,
    source_map: dict[str, str],
    candidate_reasons: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    for module in modules:
        current_effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND scope='module' ORDER BY effect",
            (module,),
            None,
        )
        has_effect = effect in current_effects
        action = "verify_no_change" if has_effect else "add_module_effect"
        source = source_map.get(module, "unknown")
        edit_path = _source_to_nico_edit_path(source)
        reason = candidate_reasons.get(module, "candidate_module")
        lines.append(
            f"- {module} edit_path={edit_path} action={action} effect={effect} "
            f"current_module_effects={_compact_list(current_effects)} reason={reason}"
        )
    return lines or ["- none"]


def _source_edit_plan_lines(
    conn: sqlite3.Connection,
    module: str,
    provider: str,
    dependent_rows: list[dict[str, Any]],
    candidate_modules: list[str],
    type_name: str | None,
    function: str | None,
    effect: str | None,
    source_map: dict[str, str],
    candidate_reasons: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    if type_name:
        lines.extend(_provider_type_edit_plan_lines(
            conn,
            provider,
            type_name,
            source_map,
            dependent_rows,
            candidate_reasons,
        ))
    if function:
        lines.extend(_function_effect_edit_plan_lines(conn, module, function, effect, source_map))
    if effect:
        lines.extend(_module_effect_edit_plan_lines(
            conn,
            candidate_modules,
            effect,
            source_map,
            candidate_reasons,
        ))
    if not lines:
        return ["- none (type_name, function, or effect required)"]
    return lines


def _provider_type_edit_plan_lines(
    conn: sqlite3.Connection,
    provider: str,
    type_name: str,
    source_map: dict[str, str],
    dependent_rows: list[dict[str, Any]],
    candidate_reasons: dict[str, str],
) -> list[str]:
    edit_path = _source_to_nico_edit_path(source_map.get(provider, "unknown"))
    lines = [
        f"- {provider} edit_path={edit_path} action=update_type_surface "
        f"type={type_name} categories=interface_type,interface_function,"
        "implementation,checks_examples,imports_effects "
        "required_edit=true reason=provider_module_type_shape_changed"
    ]
    for row in dependent_rows:
        module = row["module"]
        dep_path = _source_to_nico_edit_path(source_map.get(module, "unknown"))
        examples = _module_examples(conn, module)
        example_text = f" examples={_compact_list(examples)}" if examples else ""
        reason = candidate_reasons.get(module, "type_dependent")
        lines.append(
            f"- {module} edit_path={dep_path} action=review_type_dependency "
            f"type={type_name} categories=imports,checks_examples,call_sites "
            f"required_edit=true{example_text} reason={reason}"
        )
    return lines


def _function_effect_edit_plan_lines(
    conn: sqlite3.Connection,
    module: str,
    function: str,
    effect: str | None,
    source_map: dict[str, str],
) -> list[str]:
    if not effect:
        return [
            f"- {module}.{function} action=none required_edit=false "
            "reason=function_effect_plan_requires_effect_filter"
        ]
    current_effects = _filtered_effects(
        conn,
        "SELECT effect FROM effects WHERE module_name=? AND function_name=? ORDER BY effect",
        (module, function),
        None,
    )
    has_effect = effect in current_effects
    action = "verify_function_no_change" if has_effect else "add_function_effect"
    required = not has_effect
    edit_path = _source_to_nico_edit_path(source_map.get(module, "unknown"))
    no_op = " no_op_edit=forbidden" if has_effect else ""
    lines = [
        f"- {module}.{function} edit_path={edit_path} action={action} "
        f"effect={effect} current_function_effects={_compact_list(current_effects)} "
        f"required_edit={str(required).lower()}"
        f"{no_op} reason=function_effect_update"
    ]
    for caller in _reverse_call_paths(conn, module, function, transitive=True):
        caller_module, caller_fn = _split_fn_label(caller["caller"])
        caller_effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND function_name=? ORDER BY effect",
            (caller_module, caller_fn),
            None,
        )
        caller_module_effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND scope='module' ORDER BY effect",
            (caller_module,),
            None,
        )
        caller_has_effect = effect in caller_effects or effect in caller_module_effects
        caller_required = not caller_has_effect
        caller_no_op = " no_op_edit=forbidden" if caller_has_effect else ""
        lines.append(
            f"- {caller['caller']} edit_path={_source_to_nico_edit_path(source_map.get(caller_module, 'unknown'))} "
            f"action=review_caller_effect_boundary effect={effect} "
            f"current_function_effects={_compact_list(caller_effects)} "
            f"current_module_effects={_compact_list(caller_module_effects)} "
            f"required_edit={str(caller_required).lower()}{caller_no_op} "
            f"reason=caller_via_call_path:{' -> '.join(caller['path'])}"
        )
    return lines


def _module_effect_edit_plan_lines(
    conn: sqlite3.Connection,
    modules: list[str],
    effect: str,
    source_map: dict[str, str],
    candidate_reasons: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    for module in modules:
        current_effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND scope='module' ORDER BY effect",
            (module,),
            None,
        )
        has_effect = effect in current_effects
        action = "verify_no_change" if has_effect else "add_module_effect"
        required = not has_effect
        edit_path = _source_to_nico_edit_path(source_map.get(module, "unknown"))
        reason = candidate_reasons.get(module, "candidate_module")
        no_op = " no_op_edit=forbidden" if has_effect else ""
        lines.append(
            f"- {module} edit_path={edit_path} action={action} effect={effect} "
            f"current_module_effects={_compact_list(current_effects)} "
            f"required_edit={str(required).lower()}"
            f"{no_op} reason={reason}"
        )
    return lines or ["- none"]


def _module_examples(conn: sqlite3.Connection, module: str) -> list[str]:
    return [
        row["path"] or row["example_id"]
        for row in conn.execute(
            "SELECT example_id, path FROM examples WHERE module_name=? ORDER BY example_id",
            (module,),
        ).fetchall()
    ]


def _split_fn_label(label: str) -> tuple[str, str]:
    module, _, function = label.rpartition(".")
    return module, function


def _source_to_nico_edit_path(source: str) -> str:
    if source.endswith(".rs"):
        return source[:-3] + ".nico"
    if source.endswith(".nico"):
        return source
    return source or "unknown"


def _column(conn: sqlite3.Connection, sql: str, *params: str) -> list[str]:
    return [row[0] for row in conn.execute(sql, params).fetchall()]


def _group_rows(rows: list[sqlite3.Row], key_col: str, value_col: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row[key_col]].append(row[value_col])
    return dict(grouped)


def _compact_list(values: list[Any]) -> str:
    cleaned = [str(value) for value in values if value is not None and str(value)]
    return ", ".join(cleaned) if cleaned else "none"


def _reverse_import_paths(
    conn: sqlite3.Connection,
    target: str,
    transitive: bool,
) -> list[dict[str, Any]]:
    reverse: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute(
        "SELECT module_name, imported_module FROM imports ORDER BY module_name, imported_module"
    ):
        reverse[row["imported_module"]].append(row["module_name"])

    queue = deque((importer, 1, [importer, target]) for importer in reverse.get(target, []))
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    while queue:
        module, depth, path = queue.popleft()
        if module in seen:
            continue
        seen.add(module)
        results.append({"module": module, "depth": depth, "path": path})
        if not transitive:
            continue
        for importer in reverse.get(module, []):
            if importer not in seen:
                queue.append((importer, depth + 1, [importer] + path))
    return sorted(results, key=lambda item: (item["depth"], item["module"]))


def _reverse_call_paths(
    conn: sqlite3.Connection,
    module: str,
    function: str,
    transitive: bool,
) -> list[dict[str, Any]]:
    reverse: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT caller_module, caller_fn, callee_module, callee_fn FROM call_graph "
        "ORDER BY caller_module, caller_fn, callee_module, callee_fn"
    ):
        reverse[(row["callee_module"], row["callee_fn"])].append((row["caller_module"], row["caller_fn"]))

    target_label = _fn_label(module, function)
    queue = deque(
        (caller_module, caller_fn, 1, [_fn_label(caller_module, caller_fn), target_label])
        for caller_module, caller_fn in reverse.get((module, function), [])
    )
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    while queue:
        caller_module, caller_fn, depth, path = queue.popleft()
        key = (caller_module, caller_fn)
        if key in seen:
            continue
        seen.add(key)
        caller_label = _fn_label(caller_module, caller_fn)
        results.append({"caller": caller_label, "depth": depth, "path": path})
        if not transitive:
            continue
        for next_module, next_fn in reverse.get(key, []):
            if (next_module, next_fn) not in seen:
                queue.append((next_module, next_fn, depth + 1, [_fn_label(next_module, next_fn)] + path))
    return sorted(results, key=lambda item: (item["depth"], item["caller"]))


def _forward_call_paths(
    conn: sqlite3.Connection,
    module: str,
    function: str,
    effect_filter: str | None,
) -> list[dict[str, Any]]:
    forward: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT caller_module, caller_fn, callee_module, callee_fn FROM call_graph "
        "ORDER BY caller_module, caller_fn, callee_module, callee_fn"
    ):
        forward[(row["caller_module"], row["caller_fn"])].append((row["callee_module"], row["callee_fn"]))

    root = (module, function)
    root_label = _fn_label(module, function)
    queue = deque((callee_module, callee_fn, [root_label, _fn_label(callee_module, callee_fn)])
                  for callee_module, callee_fn in forward.get(root, []))
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    while queue:
        callee_module, callee_fn, path = queue.popleft()
        key = (callee_module, callee_fn)
        if key in seen:
            continue
        seen.add(key)
        effects = _filtered_effects(
            conn,
            "SELECT effect FROM effects WHERE module_name=? AND function_name=? ORDER BY effect",
            (callee_module, callee_fn),
            effect_filter,
        )
        if not effect_filter or effects:
            results.append({"path": path, "effects": effects})
        for next_module, next_fn in forward.get(key, []):
            if (next_module, next_fn) not in seen:
                queue.append((next_module, next_fn, path + [_fn_label(next_module, next_fn)]))
    return results


def _filtered_effects(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[str, ...],
    effect_filter: str | None,
) -> list[str]:
    effects = [row[0] for row in conn.execute(sql, params).fetchall()]
    if effect_filter:
        effects = [effect for effect in effects if effect == effect_filter]
    return effects


def _fn_label(module: str, function: str) -> str:
    return f"{module}.{function}"
