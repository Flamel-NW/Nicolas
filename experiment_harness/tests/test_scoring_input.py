import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evaluate
import run_experiment


class ScoringInputTest(unittest.TestCase):
    def edit_tool_call(self, *, status: str = "applied") -> dict:
        output = (
            f"status: {status}\n"
            "path: src/user/types.nico\n"
            "edits: 2\n"
            "- edit #1: replace_identifier section=implementation matches=1\n"
            "- edit #2: replace_text section=surface matches=1\n"
            "diff_truncated: false\n"
            "diff:\n"
            "--- before/src/user/types.nico\n"
            "+++ after/src/user/types.nico\n"
        )
        return {
            "turn": 2,
            "tool": "edit_nico",
            "input": {
                "path": "src/user/types.nico",
                "dry_run": False,
                "edits": [{"op": "replace_identifier"}, {"op": "replace_text"}],
            },
            "output_preview": output[:120],
            "full_output": output,
        }

    def changeset(self) -> dict:
        return {
            "schema": "task-workspace-changeset-v1",
            "summary": {"added": 0, "modified": 1, "deleted": 0},
            "changed_files": ["src/user/types.nico"],
            "changes": [{"path": "src/user/types.nico", "status": "modified"}],
            "diffs": [{
                "path": "src/user/types.nico",
                "status": "modified",
                "diff": "--- before\n+++ after\n",
                "diff_truncated": False,
            }],
        }

    def test_summarize_write_tool_calls_parses_edit_nico_output(self) -> None:
        tool_calls = [
            {"turn": 1, "tool": "semantic_query", "input": {}, "full_output": "ok"},
            self.edit_tool_call(),
        ]

        summary = run_experiment.summarize_write_tool_calls(tool_calls)

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["call_index"], 2)
        self.assertEqual(summary[0]["turn"], 2)
        self.assertEqual(summary[0]["path"], "src/user/types.nico")
        self.assertEqual(summary[0]["edit_count"], 2)
        self.assertEqual(summary[0]["result_status"], "applied")
        self.assertIs(summary[0]["diff_truncated"], False)

    def test_validation_summary_warns_when_files_changed_without_edit_tool(self) -> None:
        summary = run_experiment.build_validation_summary(None, self.changeset(), [])

        self.assertEqual(summary["changed_files_count"], 1)
        self.assertIn(
            "workspace files changed without any recorded edit_nico call",
            summary["warnings"],
        )

    def test_build_scoring_input_includes_changeset_and_write_calls(self) -> None:
        changeset = self.changeset()
        write_calls = run_experiment.summarize_write_tool_calls([self.edit_tool_call()])
        validation = run_experiment.build_validation_summary(None, changeset, write_calls)

        scoring_input = run_experiment.build_scoring_input(
            "Summary:\n- changed user.types",
            {"path": "workspaces/batch/run"},
            changeset,
            write_calls,
            validation,
        )

        self.assertEqual(scoring_input["schema"], "t3-scoring-input-v1")
        self.assertEqual(scoring_input["workspace_path"], "workspaces/batch/run")
        self.assertEqual(scoring_input["changed_files"], ["src/user/types.nico"])
        self.assertEqual(scoring_input["compact_diffs"][0]["path"], "src/user/types.nico")
        self.assertEqual(scoring_input["write_tool_calls"][0]["result_status"], "applied")
        self.assertEqual(scoring_input["validation_summary"]["applied_edit_count"], 2)

    def test_validation_summary_marks_audit_risk_flags(self) -> None:
        changeset = self.changeset()
        write_calls = run_experiment.summarize_write_tool_calls([
            self.edit_tool_call(status="error"),
        ])
        validation = run_experiment.build_validation_summary(
            None,
            changeset,
            write_calls,
            response="Summary:\n- changed user.types",
            turns=25,
            max_turns=25,
        )

        self.assertIs(validation["audit_risk"], True)
        self.assertIn("edit_nico_error", validation["audit_risk_flags"])
        self.assertIn("max_turns_reached", validation["audit_risk_flags"])
        self.assertIn("final_answer_protocol_missing", validation["audit_risk_flags"])

    def test_evaluator_falls_back_to_workspace_changeset_for_old_results(self) -> None:
        record = {
            "response": "Summary:\n- changed user.types",
            "workspace": {
                "path": "workspaces/batch/run",
                "changeset": self.changeset(),
            },
            "tool_calls": [self.edit_tool_call()],
        }

        scoring_input = evaluate.build_scoring_input_from_record(record)

        self.assertEqual(scoring_input["schema"], "evaluator-fallback-scoring-input-v1")
        self.assertEqual(scoring_input["changed_files"], ["src/user/types.nico"])
        self.assertEqual(scoring_input["write_tool_calls"][0]["result_status"], "applied")


if __name__ == "__main__":
    unittest.main()
