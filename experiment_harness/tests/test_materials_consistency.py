import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


HARNESS = Path(__file__).resolve().parents[1]
REPO = HARNESS.parent


class E3MaterialsConsistencyTest(unittest.TestCase):
    def test_e3_cache_key_ttl_facts_match_acd_public_and_dbs(self) -> None:
        nico_paths = [
            REPO / "src/cache/kv.nico",
            HARNESS / "materials/condition_C/e3/kv.nico",
            HARNESS / "materials/condition_C/e3/src/cache/kv.nico",
        ]
        for path in nico_paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("fn new_key(s: String) -> CacheKey", text)
                self.assertIn("fn new_ttl(secs: u64) -> CacheTtl", text)
                self.assertIn("pub struct CacheKey(pub String);", text)
                self.assertIn("pub struct CacheTtl(pub u64);", text)

        rust_paths = [
            REPO / "src/cache/kv.rs",
            HARNESS / "materials/condition_A/e3/kv.rs",
            HARNESS / "materials/condition_D/e3/kv.rs",
        ]
        for path in rust_paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("pub struct CacheKey(pub String);", text)
                self.assertIn("pub struct CacheTtl(pub u64);", text)
                self.assertIn("pub fn new_key(s: String) -> CacheKey", text)
                self.assertIn("pub fn new_ttl(secs: u64) -> CacheTtl", text)

        self.assertEqual(
            self.function_names(HARNESS / "materials/sem_trusted.db", "cache.kv") & {"new_key", "new_ttl"},
            {"new_key", "new_ttl"},
        )
        self.assertEqual(
            self.function_names(
                HARNESS / "materials/semantic_db/condition_D/e3/sem_d_trusted.db",
                "cache.kv",
            ) & {"new_key", "new_ttl"},
            {"new_key", "new_ttl"},
        )

        golden = (HARNESS / "materials/golden_reference/e3.md").read_text(encoding="utf-8")
        self.assertIn("new_key", golden)
        self.assertIn("new_ttl", golden)

    def test_generated_manual_token_metadata_marks_estimates(self) -> None:
        for rel in ("materials/manual_tokens.json", "materials/manual_tokens_D.json"):
            with self.subTest(path=rel):
                data = json.loads((HARNESS / rel).read_text(encoding="utf-8"))
                self.assertIn("manual_tokens_per_turn", data)
                self.assertIn("estimated", data)

    def function_names(self, db_path: Path, module: str) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM functions WHERE module_name=?",
                (module,),
            ).fetchall()
        return {row[0] for row in rows}


if __name__ == "__main__":
    unittest.main()
