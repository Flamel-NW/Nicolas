import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_experiment
import task_workspace


class TaskWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.harness = self.root / "experiment_harness"
        self.materials = self.harness / "materials"
        self.repo = self.root / "Nicolas"
        self.src = self.repo / "src"
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

    def prepare_e1_workspace(self) -> task_workspace.TaskWorkspace:
        self.write(
            "experiment_harness/materials/condition_C/e1/types.nico",
            "module user.types { flat }\n",
        )
        self.write(
            "experiment_harness/materials/condition_C/e1/src/user/types.nico",
            "module user.types { nested }\n",
        )
        self.write(
            "experiment_harness/materials/condition_C/e1/notes.txt",
            "copied regular non-nico file\n",
        )
        return task_workspace.prepare_task_workspace(
            "E1", "C", "E1_C_v3_run01_20260613T000000Z", None, self.workspace_root
        )

    def test_prepare_copies_flat_and_nested_task_materials(self) -> None:
        workspace = self.prepare_e1_workspace()

        self.assertEqual(workspace.source_kind, "task_materials")
        self.assertEqual(workspace.copy_policy, "all_regular_files")
        self.assertTrue((workspace.root / "types.nico").exists())
        self.assertTrue((workspace.root / "src/user/types.nico").exists())
        self.assertTrue((workspace.root / "notes.txt").exists())
        self.assertTrue((workspace.root / task_workspace.MANIFEST_FILENAME).exists())
        self.assertEqual(workspace.manifest_before["file_count"], 3)

    def test_prepare_falls_back_to_src_nico_files(self) -> None:
        self.write("experiment_harness/materials/condition_C/t7/cache_kv.json", "{}\n")
        self.write("Nicolas/src/user/types.nico", "module user.types {}\n")
        self.write("Nicolas/src/cache/kv.nico", "module cache.kv {}\n")
        self.write("Nicolas/src/README.txt", "not copied\n")

        workspace = task_workspace.prepare_task_workspace(
            "T7", "C", "T7_C_v3_run01_20260613T000000Z", "batch/unsafe", self.workspace_root
        )

        self.assertEqual(workspace.source_kind, "fallback_src")
        self.assertEqual(workspace.copy_policy, "nico_files_only")
        self.assertTrue((workspace.root / "user/types.nico").exists())
        self.assertTrue((workspace.root / "cache/kv.nico").exists())
        self.assertFalse((workspace.root / "README.txt").exists())
        self.assertIn("batch_unsafe", workspace.root.as_posix())

    def test_condition_c_read_file_uses_workspace_copy(self) -> None:
        workspace = self.prepare_e1_workspace()

        nested = run_experiment._read_file_condition_C("src/user/types.nico", "E1", workspace=workspace)
        flat = run_experiment._read_file_condition_C("types.nico", "E1", workspace=workspace)

        self.assertEqual(nested, "module user.types { nested }\n")
        self.assertEqual(flat, "module user.types { flat }\n")

        (workspace.root / "src/user/types.nico").write_text("workspace changed\n", encoding="utf-8")
        baseline = self.materials / "condition_C/e1/src/user/types.nico"
        self.assertEqual(baseline.read_text(encoding="utf-8"), "module user.types { nested }\n")

    def test_compute_changeset_records_added_modified_and_deleted(self) -> None:
        self.write("experiment_harness/materials/condition_C/e1/a.nico", "old a\n")
        self.write("experiment_harness/materials/condition_C/e1/b.nico", "old b\n")
        workspace = task_workspace.prepare_task_workspace(
            "E1", "C", "E1_C_v3_run01_20260613T000001Z", None, self.workspace_root
        )

        (workspace.root / "a.nico").write_text("new a\n", encoding="utf-8")
        (workspace.root / "b.nico").unlink()
        (workspace.root / "c.nico").write_text("new c\n", encoding="utf-8")

        changeset = task_workspace.compute_changeset(workspace)

        self.assertEqual(changeset["summary"], {"added": 1, "modified": 1, "deleted": 1})
        self.assertEqual(changeset["changed_files"], ["a.nico", "b.nico", "c.nico"])
        statuses = {item["path"]: item["status"] for item in changeset["changes"]}
        self.assertEqual(statuses["a.nico"], "modified")
        self.assertEqual(statuses["b.nico"], "deleted")
        self.assertEqual(statuses["c.nico"], "added")
        self.assertTrue((workspace.root / task_workspace.CHANGESET_FILENAME).exists())

    def test_resolver_rejects_unsafe_wrong_suffix_and_ambiguous_paths(self) -> None:
        self.write("experiment_harness/materials/condition_C/e1/good.nico", "ok\n")
        workspace = task_workspace.prepare_task_workspace(
            "E1", "C", "E1_C_v3_run01_20260613T000002Z", None, self.workspace_root
        )
        (workspace.root / "a").mkdir()
        (workspace.root / "b").mkdir()
        (workspace.root / "a/dup.nico").write_text("a\n", encoding="utf-8")
        (workspace.root / "b/dup.nico").write_text("b\n", encoding="utf-8")

        resolved = task_workspace.resolve_workspace_file(workspace, "good.nico")
        self.assertEqual(resolved, workspace.root / "good.nico")

        for bad_path in ("../good.nico", "/tmp/good.nico", "good.rs", "dup.nico"):
            with self.subTest(path=bad_path):
                with self.assertRaises(task_workspace.WorkspaceError):
                    task_workspace.resolve_workspace_file(workspace, bad_path)


if __name__ == "__main__":
    unittest.main()
