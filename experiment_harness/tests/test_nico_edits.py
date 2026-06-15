import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_experiment
import task_workspace
from nico_edits import apply_nico_edits


class NicoEditsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.harness = self.root / "experiment_harness"
        self.materials = self.harness / "materials"
        self.repo = self.root / "Nicolas"
        self.workspace_root = self.harness / "workspaces"

        self.old_harness = task_workspace.HARNESS_DIR
        self.old_materials = task_workspace.MATERIALS_DIR
        self.old_nicolas_root = task_workspace.NICOLAS_ROOT
        task_workspace.HARNESS_DIR = self.harness
        task_workspace.MATERIALS_DIR = self.materials
        task_workspace.NICOLAS_ROOT = self.repo
        self.addCleanup(self.restore_paths)

    def restore_paths(self) -> None:
        task_workspace.HARNESS_DIR = self.old_harness
        task_workspace.MATERIALS_DIR = self.old_materials
        task_workspace.NICOLAS_ROOT = self.old_nicolas_root

    def write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def prepare_workspace(self, content: str, rel: str = "src/user/types.nico") -> task_workspace.TaskWorkspace:
        self.write(f"experiment_harness/materials/condition_C/e1/{rel}", content)
        return task_workspace.prepare_task_workspace(
            "E1", "C", "E1_C_v3_run01_20260613T000000Z", None, self.workspace_root
        )

    def test_section_scoped_replace_only_changes_target_section(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.types {
  spec {
    interface {
      type UserProfile
    }
  }
  checks {
    example demo {
      assert typeof(profile) == UserProfile;
    }
  }
  implementation rust {
    pub struct UserProfile {
      pub id: u64,
    }
  }
}
""".lstrip()
        )

        result = apply_nico_edits(workspace, "src/user/types.nico", [{
            "op": "replace_text",
            "section": "implementation",
            "target": "pub struct UserProfile",
            "replacement": "pub struct UserRecord",
        }])

        self.assertIn("status: applied", result)
        changed = (workspace.root / "src/user/types.nico").read_text(encoding="utf-8")
        self.assertIn("type UserProfile", changed)
        self.assertIn("typeof(profile) == UserProfile", changed)
        self.assertIn("pub struct UserRecord", changed)
        self.assertNotIn("pub struct UserProfile", changed)

    def test_replace_identifier_uses_identifier_boundaries(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.types {
  spec { interface { type UserProfile } }
  checks { }
  implementation rust {
    pub struct UserProfiled;
    pub struct UserProfile;
    pub fn load() -> UserProfile {
      todo!()
    }
  }
}
""".lstrip()
        )

        result = apply_nico_edits(workspace, "src/user/types.nico", [{
            "op": "replace_identifier",
            "section": "implementation",
            "target": "UserProfile",
            "replacement": "UserRecord",
            "expected_count": 2,
        }])

        self.assertIn("matches=2", result)
        changed = (workspace.root / "src/user/types.nico").read_text(encoding="utf-8")
        self.assertIn("UserProfiled", changed)
        self.assertIn("pub struct UserRecord", changed)
        self.assertIn("-> UserRecord", changed)
        self.assertNotIn("pub struct UserProfile;", changed)

    def test_insert_ops_cover_variant_and_new_function(self) -> None:
        workspace = self.prepare_workspace(
            """
module audit.log {
  spec {
    interface {
      type ProfileAuditAction  // ProfileViewed | ProfileUpdated
    }
  }
  checks { }
  implementation rust {
    pub enum ProfileAuditAction {
        ProfileViewed,
        ProfileUpdated,
    }
  }
}
""".lstrip(),
            rel="src/audit/log.nico",
        )

        result = apply_nico_edits(workspace, "src/audit/log.nico", [
            {
                "op": "insert_after",
                "section": "implementation",
                "target": "        ProfileUpdated,",
                "text": "\n        ProfileExported,",
            },
            {
                "op": "insert_before_section_end",
                "section": "implementation",
                "text": "\n    pub fn export_profile_marker() {}\n",
            },
        ])

        self.assertIn("edits: 2", result)
        changed = (workspace.root / "src/audit/log.nico").read_text(encoding="utf-8")
        self.assertIn("ProfileExported", changed)
        self.assertIn("pub fn export_profile_marker() {}", changed)
        self.assertLess(changed.index("pub fn export_profile_marker"), changed.rindex("\n}"))

    def test_structural_ops_insert_inside_interface_and_implementation(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.profile_service {
  spec {
    interface {
      fn get_profile(id: UserId) -> Option
        effects [reads_clock, db.read]
    }

    effects [reads_clock, db.read]
  }

  checks { }

  implementation rust {
    pub fn get_profile(_id: UserId) -> Option<UserProfile> {
        todo!()
    }
  }
}
""".lstrip(),
            rel="src/user/profile_service.nico",
        )

        result = apply_nico_edits(workspace, "src/user/profile_service.nico", [
            {
                "op": "insert_interface_item",
                "text": """
      fn suspend_user(id: UserId) -> ()
        effects [db.read, db.write, audit.write]
""".rstrip(),
            },
            {
                "op": "update_module_effects",
                "effects": ["db.write", "audit.write"],
                "mode": "merge",
            },
            {
                "op": "insert_implementation_item",
                "text": """
    pub fn suspend_user(_id: UserId) {
        todo!()
    }
""".rstrip(),
            },
        ])

        self.assertIn("status: applied", result)
        changed = (workspace.root / "src/user/profile_service.nico").read_text(encoding="utf-8")
        interface_insert = changed.index("fn suspend_user(id: UserId)")
        module_effects = changed.index("    effects [reads_clock, db.read, db.write, audit.write]")
        impl_insert = changed.index("pub fn suspend_user(_id: UserId)")
        self.assertLess(interface_insert, module_effects)
        self.assertGreater(impl_insert, changed.index("implementation rust"))

    def test_update_module_imports_and_missing_effects_insert_top_level_items(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.types {
  spec {
    intent "pure value types"

    interface {
      type UserProfile
    }

    // 无 effects：所有函数均为纯计算，不读取外部状态
    // 无 imports：user.types 不依赖任何其他模块
  }
  checks { }
  implementation rust {
    pub struct UserProfile;
  }
}
""".lstrip()
        )

        result = apply_nico_edits(workspace, "src/user/types.nico", [
            {
                "op": "update_module_imports",
                "imports": ["time.clock"],
                "mode": "merge",
            },
            {
                "op": "update_module_effects",
                "effects": ["reads_clock"],
                "mode": "merge",
            },
        ])

        self.assertIn("status: applied", result)
        changed = (workspace.root / "src/user/types.nico").read_text(encoding="utf-8")
        self.assertIn("    imports [time.clock]", changed)
        self.assertIn("    effects [reads_clock]", changed)
        self.assertNotIn("无 effects", changed)
        self.assertNotIn("无 imports", changed)
        self.assertLess(changed.index("imports [time.clock]"), changed.index("interface {"))
        self.assertLess(changed.index("interface {"), changed.index("effects [reads_clock]"))

    def test_update_module_effects_does_not_match_function_effects(self) -> None:
        workspace = self.prepare_workspace(
            """
module session.store {
  spec {
    imports [session.types]

    interface {
      fn load_session(id: SessionId) -> Option
        effects [db.read]

      fn save_session(info: SessionInfo) -> ()
        effects [db.write]

      fn revoke_session(id: SessionId) -> ()
        effects [db.read, db.write]
    }

    effects [db.read, db.write]
  }
  checks { }
  implementation rust { }
}
""".lstrip(),
            rel="src/session/store.nico",
        )

        result = apply_nico_edits(workspace, "src/session/store.nico", [{
            "op": "update_module_effects",
            "effects": ["reads_clock"],
            "mode": "merge",
        }])

        self.assertIn("status: applied", result)
        changed = (workspace.root / "src/session/store.nico").read_text(encoding="utf-8")
        self.assertIn("fn revoke_session(id: SessionId) -> ()\n        effects [db.read, db.write]", changed)
        self.assertIn("    effects [db.read, db.write, reads_clock]", changed)

    def test_structural_insert_missing_interface_is_atomic(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.profile_service {
  spec {
    effects [reads_clock]
  }
  checks { }
  implementation rust {
    pub fn get_profile() {}
  }
}
""".lstrip(),
            rel="src/user/profile_service.nico",
        )
        path = workspace.root / "src/user/profile_service.nico"
        before = path.read_text(encoding="utf-8")

        result = apply_nico_edits(workspace, "src/user/profile_service.nico", [{
            "op": "insert_interface_item",
            "text": "      fn suspend_user(id: UserId) -> ()",
        }])

        self.assertIn("Error:", result)
        self.assertIn("surface.interface", result)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_batch_is_atomic_when_later_edit_fails(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.types {
  spec { interface { type UserProfile } }
  checks { }
  implementation rust {
    pub struct UserProfile;
  }
}
""".lstrip()
        )
        path = workspace.root / "src/user/types.nico"
        before = path.read_text(encoding="utf-8")

        result = apply_nico_edits(workspace, "src/user/types.nico", [
            {
                "op": "replace_text",
                "section": "implementation",
                "target": "pub struct UserProfile",
                "replacement": "pub struct UserRecord",
            },
            {
                "op": "replace_text",
                "section": "implementation",
                "target": "pub struct UserProfil",
                "replacement": "never written",
            },
        ])

        self.assertIn("Error:", result)
        self.assertIn("section: implementation", result)
        self.assertIn("match_count: 0", result)
        self.assertIn("nearest_anchors:", result)
        self.assertIn("section_tail_context:", result)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_dry_run_does_not_write_file(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.types {
  spec { interface { type UserProfile } }
  checks { }
  implementation rust {
    pub struct UserProfile;
  }
}
""".lstrip()
        )
        path = workspace.root / "src/user/types.nico"
        before = path.read_text(encoding="utf-8")

        result = apply_nico_edits(workspace, "src/user/types.nico", [{
            "op": "replace_identifier",
            "section": "file",
            "target": "UserProfile",
            "replacement": "UserRecord",
            "expected_count": 2,
        }], dry_run=True)

        self.assertIn("status: dry_run", result)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_edit_nico_maps_rs_source_path_to_nico_workspace_file(self) -> None:
        workspace = self.prepare_workspace(
            """
module user.profile_service {
  spec {
    interface {
      fn get_profile(id: UserId) -> Option
    }
    effects [reads_clock]
  }
  checks { }
  implementation rust { }
}
""".lstrip(),
            rel="src/user/profile_service.nico",
        )

        result = apply_nico_edits(workspace, "src/user/profile_service.rs", [{
            "op": "update_module_effects",
            "effects": ["db.read"],
            "mode": "merge",
        }])

        self.assertIn("status: applied", result)
        changed = (workspace.root / "src/user/profile_service.nico").read_text(encoding="utf-8")
        self.assertIn("effects [reads_clock, db.read]", changed)

    def test_rejects_unsafe_suffix_and_non_c_tool_use(self) -> None:
        workspace = self.prepare_workspace("module user.types { spec { } checks { } implementation rust { } }\n")

        unsafe = apply_nico_edits(workspace, "../types.nico", [{
            "op": "replace_text",
            "section": "file",
            "target": "x",
            "replacement": "y",
        }])
        wrong_suffix = apply_nico_edits(workspace, "src/user/types.txt", [{
            "op": "replace_text",
            "section": "file",
            "target": "x",
            "replacement": "y",
        }])
        no_workspace = run_experiment.execute_tool("edit_nico", {
            "path": "src/user/types.nico",
            "edits": [],
        }, "C", "E1")
        condition_a = run_experiment.execute_tool("edit_nico", {
            "path": "src/user/types.nico",
            "edits": [],
        }, "A", "E1", workspace=workspace)

        self.assertIn("unsafe path rejected", unsafe)
        self.assertIn("only .nico files are accessible", wrong_suffix)
        self.assertIn("requires a condition C task workspace", no_workspace)
        self.assertIn("only available in condition C", condition_a)

    def test_changeset_records_workspace_modification_only(self) -> None:
        workspace = self.prepare_workspace(
            "module user.types { spec { } checks { } implementation rust { pub struct UserProfile; } }\n"
        )
        baseline = self.materials / "condition_C/e1/src/user/types.nico"

        apply_nico_edits(workspace, "src/user/types.nico", [{
            "op": "replace_identifier",
            "section": "implementation",
            "target": "UserProfile",
            "replacement": "UserRecord",
        }])
        changeset = task_workspace.compute_changeset(workspace)

        self.assertEqual(
            baseline.read_text(encoding="utf-8"),
            "module user.types { spec { } checks { } implementation rust { pub struct UserProfile; } }\n",
        )
        self.assertEqual(changeset["changed_files"], ["src/user/types.nico"])


if __name__ == "__main__":
    unittest.main()
