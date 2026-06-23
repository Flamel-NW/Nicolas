import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parse_condition_d


class ConditionDAnnotationValidationTest(unittest.TestCase):
    def validate(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.rs"
            path.write_text(content, encoding="utf-8")
            data = parse_condition_d.parse_annotations(path)
            return parse_condition_d.validate_annotations(path, data)

    def test_valid_annotations_pass(self) -> None:
        errors = self.validate(
            """// @nico-module: cache.example
// @nico-intent: Example.
// @nico-imports: cache.kv
// @nico-module-effects: reads_clock
// @nico-type: ExampleId | pub | opaque
// @nico-fn: touch | pub fn touch(key: CacheKey) -> () | effects=reads_clock | calls=cache.kv::get

use crate::cache::kv;
use crate::cache::kv::CacheKey;

pub struct ExampleId(u64);

pub fn touch(_key: CacheKey) {
    let _ = kv::get(_key);
}
"""
        )
        self.assertEqual(errors, [])

    def test_stale_type_annotation_fails(self) -> None:
        errors = self.validate(
            """// @nico-module: user.types
// @nico-intent: Example.
// @nico-imports:
// @nico-module-effects:
// @nico-type: UserProfile | pub | struct

pub struct UserRecord {}
"""
        )
        self.assertTrue(any("@nico-type 'UserProfile'" in error for error in errors))

    def test_stale_function_signature_type_fails(self) -> None:
        errors = self.validate(
            """// @nico-module: user.store
// @nico-intent: Example.
// @nico-imports: user.types
// @nico-module-effects: db.write
// @nico-fn: save_profile | pub fn save_profile(profile: UserProfile) -> () | effects=db.write | calls=

pub struct UserRecord {}

pub fn save_profile(_profile: UserRecord) {}
"""
        )
        self.assertTrue(any("signature mentions 'UserProfile'" in error for error in errors))

    def test_missing_semantic_import_fails(self) -> None:
        errors = self.validate(
            """// @nico-module: rate.limiter
// @nico-intent: Example.
// @nico-imports: time.clock
// @nico-module-effects: reads_clock
// @nico-fn: write_window | pub fn write_window(key: CacheKey) -> () | effects=reads_clock | calls=cache.kv::set

use crate::cache::kv;
use crate::cache::kv::CacheKey;

pub fn write_window(_key: CacheKey) {
    kv::set(_key, String::new(), crate::cache::kv::CacheTtl(60));
}
"""
        )
        self.assertTrue(any("omits it" in error and "cache.kv" in error for error in errors))

    def test_stale_semantic_import_fails(self) -> None:
        errors = self.validate(
            """// @nico-module: rate.limiter
// @nico-intent: Example.
// @nico-imports: cache.kv
// @nico-module-effects:
// @nico-fn: write_window | pub fn write_window() -> () | effects= | calls=

pub fn write_window() {}
"""
        )
        self.assertTrue(any("@nico-imports lists 'cache.kv'" in error for error in errors))

    def test_stale_call_annotation_fails(self) -> None:
        errors = self.validate(
            """// @nico-module: rate.limiter
// @nico-intent: Example.
// @nico-imports: cache.kv
// @nico-module-effects: reads_clock
// @nico-fn: write_window | pub fn write_window(key: CacheKey) -> () | effects=reads_clock | calls=cache.kv::set

use crate::cache::kv;
use crate::cache::kv::CacheKey;

pub fn write_window(_key: CacheKey) {
    let _ = kv::get(_key);
}
"""
        )
        self.assertTrue(any("call cache.kv::set" in error for error in errors))

    def test_stale_effect_annotation_fails_for_known_effects(self) -> None:
        errors = self.validate(
            """// @nico-module: cache.example
// @nico-intent: Example.
// @nico-imports:
// @nico-module-effects: reads_clock
// @nico-fn: touch | pub fn touch() -> () | effects=reads_clock | calls=

pub fn touch() {}
"""
        )
        self.assertTrue(any("effect 'reads_clock'" in error for error in errors))

    def test_manual_token_fallback_reports_estimated(self) -> None:
        fake_anthropic = types.SimpleNamespace(
            Anthropic=lambda: (_ for _ in ()).throw(RuntimeError("token API unavailable"))
        )
        with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
            tokens, estimated = parse_condition_d.count_manual_tokens("abc" * 30)

        self.assertGreater(tokens, 0)
        self.assertIs(estimated, True)


if __name__ == "__main__":
    unittest.main()
