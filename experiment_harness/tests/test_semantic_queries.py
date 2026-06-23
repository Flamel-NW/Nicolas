import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semantic_queries import run_semantic_query


REMOVED_E1_OP = "update_user_profile" + "_timestamp_surface"
REMOVED_QUERY_HINT = "preferred" + "_op"


SCHEMA = """
CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT, source TEXT, schema_version TEXT);
CREATE TABLE imports (id INTEGER PRIMARY KEY, module_name TEXT, imported_module TEXT);
CREATE TABLE types (id INTEGER PRIMARY KEY, module_name TEXT, name TEXT, visibility TEXT, repr TEXT);
CREATE TABLE functions (id INTEGER PRIMARY KEY, module_name TEXT, name TEXT, signature TEXT, visibility TEXT);
CREATE TABLE effects (id INTEGER PRIMARY KEY, module_name TEXT, function_name TEXT, effect TEXT, scope TEXT);
CREATE TABLE examples (id INTEGER PRIMARY KEY, module_name TEXT, example_id TEXT, path TEXT);
CREATE TABLE propagated_effects (id INTEGER PRIMARY KEY, module_name TEXT, effect TEXT, source_module TEXT, depth INTEGER);
CREATE TABLE call_graph (id INTEGER PRIMARY KEY, caller_module TEXT, caller_fn TEXT, callee_module TEXT, callee_fn TEXT);
"""


class SemanticQueriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "sem_trusted.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        self.seed(conn)
        conn.commit()
        conn.close()

    def seed(self, conn: sqlite3.Connection) -> None:
        modules = [
            ("time.clock", "src/time/clock.rs"),
            ("cache.kv", "src/cache/kv.rs"),
            ("user.types", "src/user/types.rs"),
            ("user.store", "src/user/store.rs"),
            ("audit.log", "src/audit/log.rs"),
            ("user.profile_service", "src/user/profile_service.rs"),
            ("user.admin_service", "src/user/admin_service.rs"),
            ("session.types", "src/session/types.rs"),
            ("session.store", "src/session/store.rs"),
            ("session.service", "src/session/service.rs"),
            ("metrics.recorder", "src/metrics/recorder.rs"),
            ("rate.limiter", "src/rate/limiter.rs"),
        ]
        conn.executemany(
            "INSERT INTO modules (name, source, schema_version) VALUES (?, ?, 'test')",
            modules,
        )
        conn.executemany(
            "INSERT INTO imports (module_name, imported_module) VALUES (?, ?)",
            [
                ("cache.kv", "time.clock"),
                ("user.store", "user.types"),
                ("audit.log", "user.types"),
                ("user.profile_service", "user.types"),
                ("user.profile_service", "cache.kv"),
                ("user.profile_service", "user.store"),
                ("user.profile_service", "audit.log"),
                ("user.admin_service", "user.profile_service"),
                ("session.types", "user.types"),
                ("session.store", "session.types"),
                ("session.service", "session.store"),
                ("session.service", "cache.kv"),
                ("rate.limiter", "cache.kv"),
                ("rate.limiter", "metrics.recorder"),
            ],
        )
        conn.executemany(
            "INSERT INTO types (module_name, name, visibility, repr) VALUES (?, ?, 'pub', ?)",
            [
                ("cache.kv", "CacheKey", "newtype"),
                ("cache.kv", "CacheTtl", "newtype"),
                ("user.types", "UserProfile", "struct"),
            ],
        )
        conn.executemany(
            "INSERT INTO functions (module_name, name, signature, visibility) VALUES (?, ?, ?, 'pub')",
            [
                ("time.clock", "now", "pub fn now() -> Timestamp"),
                ("cache.kv", "get", "pub fn get(key: CacheKey) -> Option"),
                ("cache.kv", "set", "pub fn set(key: CacheKey, value: String, ttl: CacheTtl) -> ()"),
                ("user.store", "mark_deactivated", "pub fn mark_deactivated(id: UserId) -> ()"),
                ("audit.log", "record", "pub fn record(event: AuditEvent) -> ()"),
                ("user.profile_service", "get_profile", "pub fn get_profile(id: UserId) -> Option"),
                ("user.profile_service", "update_profile", "pub fn update_profile(profile: UserProfile) -> ()"),
            ],
        )
        conn.executemany(
            "INSERT INTO effects (module_name, function_name, effect, scope) VALUES (?, ?, ?, ?)",
            [
                ("time.clock", "now", "reads_clock", "function"),
                ("time.clock", None, "reads_clock", "module"),
                ("cache.kv", "get", "reads_clock", "function"),
                ("cache.kv", "set", "reads_clock", "function"),
                ("cache.kv", None, "reads_clock", "module"),
                ("user.store", "mark_deactivated", "db.read", "function"),
                ("user.store", "mark_deactivated", "db.write", "function"),
                ("user.store", None, "db.read", "module"),
                ("user.store", None, "db.write", "module"),
                ("audit.log", "record", "audit.write", "function"),
                ("audit.log", None, "audit.write", "module"),
                ("user.profile_service", "get_profile", "reads_clock", "function"),
                ("user.profile_service", "get_profile", "db.read", "function"),
                ("user.profile_service", "get_profile", "audit.write", "function"),
                ("user.profile_service", None, "reads_clock", "module"),
                ("user.profile_service", None, "db.read", "module"),
                ("user.profile_service", None, "audit.write", "module"),
                ("session.service", None, "reads_clock", "module"),
                ("rate.limiter", None, "metrics.write", "module"),
                ("rate.limiter", None, "reads_clock", "module"),
            ],
        )
        conn.executemany(
            "INSERT INTO propagated_effects (module_name, effect, source_module, depth) VALUES (?, ?, ?, ?)",
            [
                ("cache.kv", "reads_clock", "time.clock", 1),
                ("user.profile_service", "reads_clock", "cache.kv", 1),
                ("user.profile_service", "db.read", "user.store", 1),
                ("user.profile_service", "audit.write", "audit.log", 1),
                ("user.admin_service", "reads_clock", "user.profile_service", 1),
                ("session.service", "reads_clock", "cache.kv", 1),
            ],
        )
        conn.executemany(
            "INSERT INTO examples (module_name, example_id, path) VALUES (?, ?, ?)",
            [("cache.kv", "set_and_get", "examples/cache_kv_set_and_get.rs")],
        )
        conn.executemany(
            "INSERT INTO call_graph (caller_module, caller_fn, callee_module, callee_fn) VALUES (?, ?, ?, ?)",
            [
                ("cache.kv", "get", "time.clock", "now"),
                ("cache.kv", "set", "time.clock", "now"),
                ("user.profile_service", "get_profile", "cache.kv", "get"),
                ("user.profile_service", "get_profile", "user.store", "mark_deactivated"),
                ("user.profile_service", "get_profile", "audit.log", "record"),
                ("user.profile_service", "update_profile", "cache.kv", "set"),
            ],
        )

    def test_module_surface(self) -> None:
        out = run_semantic_query({"query": "module_surface", "module": "cache.kv"}, self.db_path)

        self.assertIn("module: cache.kv", out)
        self.assertIn("imports: time.clock", out)
        self.assertIn("pub fn set", out)
        self.assertIn("module_effects: reads_clock", out)
        self.assertIn("set_and_get", out)

    def test_module_and_type_dependents(self) -> None:
        module_out = run_semantic_query(
            {"query": "module_dependents", "module": "cache.kv", "transitive": True},
            self.db_path,
        )
        type_out = run_semantic_query(
            {"query": "type_dependents", "type_name": "UserProfile"},
            self.db_path,
        )

        self.assertIn("user.profile_service depth=1", module_out)
        self.assertIn("user.admin_service depth=2", module_out)
        self.assertIn("type: user.types.UserProfile", type_out)
        self.assertIn("user.profile_service", type_out)

    def test_function_callers_and_effect_chain(self) -> None:
        callers = run_semantic_query(
            {"query": "function_callers", "module": "cache.kv", "function": "set"},
            self.db_path,
        )
        chain = run_semantic_query(
            {
                "query": "effect_chain",
                "module": "user.profile_service",
                "function": "get_profile",
                "effect": "db.read",
            },
            self.db_path,
        )

        self.assertIn("user.profile_service.update_profile", callers)
        self.assertIn("direct_function_effects: db.read", chain)
        self.assertIn("user.store.mark_deactivated", chain)
        self.assertIn("callee_effects=db.read", chain)

    def test_affected_modules_lists_trusted_candidates_and_effect_context(self) -> None:
        out = run_semantic_query(
            {
                "query": "affected_modules",
                "module": "user.types",
                "type_name": "UserProfile",
                "effect": "reads_clock",
            },
            self.db_path,
        )

        self.assertIn("direct_type_provider: user.types.UserProfile", out)
        self.assertIn("session.types depth=1", out)
        self.assertIn("session.service depth=3", out)
        self.assertIn("user.profile_service depth=1", out)
        self.assertIn("user.admin_service depth=2", out)
        self.assertIn("matching_propagated_effects:", out)
        self.assertIn("user.profile_service: reads_clock <- cache.kv depth=1", out)
        self.assertIn("effect_update_candidates:", out)
        self.assertIn("user.types action=add_effect has_effect=false", out)
        self.assertIn("user.profile_service action=verify_no_change has_effect=true", out)
        self.assertIn("source_effect_update_plan:", out)
        self.assertIn(
            "user.types edit_path=src/user/types.nico action=add_module_effect effect=reads_clock",
            out,
        )
        self.assertIn(
            "audit.log edit_path=src/audit/log.nico action=add_module_effect effect=reads_clock",
            out,
        )
        self.assertIn(
            "session.types edit_path=src/session/types.nico action=add_module_effect effect=reads_clock",
            out,
        )
        self.assertIn(
            "user.admin_service edit_path=src/user/admin_service.nico action=add_module_effect effect=reads_clock",
            out,
        )
        self.assertIn(
            "user.store edit_path=src/user/store.nico action=add_module_effect effect=reads_clock",
            out,
        )
        self.assertIn(
            "session.store edit_path=src/session/store.nico action=add_module_effect effect=reads_clock",
            out,
        )
        self.assertIn(
            "user.profile_service edit_path=src/user/profile_service.nico action=verify_no_change effect=reads_clock",
            out,
        )
        self.assertIn(
            "session.service edit_path=src/session/service.nico action=verify_no_change effect=reads_clock",
            out,
        )
        self.assertIn("source_edit_plan:", out)
        self.assertIn(
            "user.types edit_path=src/user/types.nico action=update_type_surface "
            "type=UserProfile categories=interface_type,interface_function,implementation,checks_examples,imports_effects",
            out,
        )
        self.assertNotIn(REMOVED_QUERY_HINT, out)
        self.assertNotIn(REMOVED_E1_OP, out)
        self.assertNotIn("ops=", out)
        self.assertIn(
            "user.admin_service edit_path=src/user/admin_service.nico action=add_module_effect "
            "effect=reads_clock",
            out,
        )
        self.assertIn("required_edit=true", out)
        self.assertIn(
            "user.profile_service edit_path=src/user/profile_service.nico action=verify_no_change "
            "effect=reads_clock",
            out,
        )
        self.assertIn("required_edit=false no_op_edit=forbidden", out)

    def test_affected_modules_source_effect_plan_for_cache_metrics(self) -> None:
        out = run_semantic_query(
            {
                "query": "affected_modules",
                "module": "cache.kv",
                "effect": "metrics.write",
            },
            self.db_path,
        )

        self.assertIn("source_effect_update_plan:", out)
        self.assertIn(
            "cache.kv edit_path=src/cache/kv.nico action=add_module_effect effect=metrics.write",
            out,
        )
        self.assertIn(
            "session.service edit_path=src/session/service.nico action=add_module_effect effect=metrics.write",
            out,
        )
        self.assertIn(
            "user.profile_service edit_path=src/user/profile_service.nico action=add_module_effect effect=metrics.write",
            out,
        )
        self.assertIn(
            "rate.limiter edit_path=src/rate/limiter.nico action=verify_no_change effect=metrics.write",
            out,
        )
        self.assertIn(
            "rate.limiter edit_path=src/rate/limiter.nico action=verify_no_change "
            "effect=metrics.write",
            out,
        )
        self.assertIn("no_op_edit=forbidden", out)

    def test_affected_modules_function_effect_plan_for_cache_metrics(self) -> None:
        out = run_semantic_query(
            {
                "query": "affected_modules",
                "module": "cache.kv",
                "function": "get",
                "effect": "metrics.write",
            },
            self.db_path,
        )

        self.assertIn("function_filter: get", out)
        self.assertIn("source_edit_plan:", out)
        self.assertIn(
            "cache.kv.get edit_path=src/cache/kv.nico action=add_function_effect "
            "effect=metrics.write current_function_effects=reads_clock "
            "op=update_interface_function_effects required_edit=true",
            out,
        )


if __name__ == "__main__":
    unittest.main()
