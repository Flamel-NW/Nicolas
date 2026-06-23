import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evaluate
import run_experiment
from task_workspace import TaskWorkspace


class FakeUsage:
    input_tokens = 10
    output_tokens = 1


class FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeResponse:
    usage = FakeUsage()

    def __init__(self, stop_reason: str, content: list | None = None) -> None:
        self.stop_reason = stop_reason
        self.content = content or []


class FakeMessages:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse("tool_use")


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.messages = FakeMessages(responses)


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
            termination_status="max_turns_exhausted",
        )

        self.assertIs(validation["audit_risk"], True)
        self.assertIn("edit_nico_error", validation["audit_risk_flags"])
        self.assertIn("max_turns_reached", validation["audit_risk_flags"])
        self.assertIn("final_answer_protocol_missing", validation["audit_risk_flags"])

    def test_validation_summary_does_not_mark_clean_end_turn_at_max_turn(self) -> None:
        response = """
Summary:
- changed user.types

Evidence:
- edit_nico

Boundary/effects check:
- no boundary violation

Validation status:
- edits applied through edit_nico
""".strip()

        validation = run_experiment.build_validation_summary(
            None,
            self.changeset(),
            run_experiment.summarize_write_tool_calls([self.edit_tool_call()]),
            response=response,
            turns=25,
            max_turns=25,
            stop_reason="end_turn",
            termination_status="clean_end_turn",
        )

        self.assertNotIn("max_turns_reached", validation["audit_risk_flags"])
        self.assertNotIn("final_answer_protocol_missing", validation["audit_risk_flags"])

    def test_validation_summary_flags_blank_response_and_unexpected_stop(self) -> None:
        validation = run_experiment.build_validation_summary(
            None,
            self.changeset(),
            run_experiment.summarize_write_tool_calls([self.edit_tool_call()]),
            response="   ",
            turns=3,
            max_turns=25,
            stop_reason="stop_sequence",
            termination_status="unexpected_stop_reason",
        )

        self.assertIn("final_answer_protocol_missing", validation["audit_risk_flags"])
        self.assertIn("unexpected_stop_reason", validation["audit_risk_flags"])

    def test_validation_summary_marks_malformed_changed_nico_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src/user/types.nico"
            source.parent.mkdir(parents=True)
            source.write_text(
                """
module user.types {
  spec {
    interface {
      fn new_profile(id: UserId) -> UserProfile}
    effects [reads_clock]
  }
  checks { }
  implementation rust { }
}
""".lstrip(),
                encoding="utf-8",
            )
            workspace = TaskWorkspace(
                root=root,
                source_root=root,
                source_kind="test",
                task="E1",
                condition="C",
                run_id="run",
                batch_id="batch",
                copy_policy="all_regular_files",
            )
            changeset = self.changeset()

            validation = run_experiment.build_validation_summary(
                workspace,
                changeset,
                run_experiment.summarize_write_tool_calls([self.edit_tool_call()]),
            )

            self.assertIn("source_validation_error", validation["audit_risk_flags"])
            self.assertIn("source_structure_invalid", validation["audit_risk_flags"])

    def test_final_answer_protocol_accepts_markdown_and_bold_headings(self) -> None:
        response = """
## Summary
- changed user.profile_service

**Evidence:**
- semantic_query(module_surface)

### Boundary/effects check
- no boundary violation

__Validation status:__
- edits applied through edit_nico
""".strip()

        validation = run_experiment.build_validation_summary(
            None,
            self.changeset(),
            run_experiment.summarize_write_tool_calls([self.edit_tool_call()]),
            response=response,
        )

        self.assertNotIn("final_answer_protocol_missing", validation["audit_risk_flags"])

    def test_final_answer_protocol_rejects_analysis_only_response(self) -> None:
        response = """
**Analysis:**
- I have a plan.

**Plan:**
- Apply edits.
""".strip()

        validation = run_experiment.build_validation_summary(
            None,
            self.changeset(),
            run_experiment.summarize_write_tool_calls([self.edit_tool_call()]),
            response=response,
        )

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
        self.assertIs(scoring_input["evidence_integrity"]["self_contained"], False)
        self.assertIn("missing_scoring_input", scoring_input["evidence_integrity"]["validation_errors"])
        self.assertEqual(scoring_input["changed_files"], ["src/user/types.nico"])
        self.assertEqual(scoring_input["write_tool_calls"][0]["result_status"], "applied")

    def test_evaluator_rejects_stale_scoring_input_and_marks_fallback(self) -> None:
        record = {
            "response": "Summary:\n- changed user.types",
            "workspace": {
                "path": "workspaces/batch/run",
                "changeset": self.changeset(),
            },
            "workspace_changeset": self.changeset(),
            "write_tool_calls": run_experiment.summarize_write_tool_calls([self.edit_tool_call()]),
            "validation_summary": {"audit_risk": False, "audit_risk_flags": []},
            "scoring_input": {
                "schema": "t3-scoring-input-v1",
                "final_answer": "stale",
                "workspace_path": "workspaces/batch/run",
                "changed_files": [],
                "changeset_summary": None,
                "compact_diffs": [],
                "write_tool_calls": [],
                "validation_summary": None,
            },
        }

        scoring_input = evaluate.build_scoring_input_from_record(record)

        self.assertEqual(scoring_input["schema"], "evaluator-fallback-scoring-input-v1")
        self.assertIs(scoring_input["evidence_integrity"]["self_contained"], False)
        self.assertIn("changed_files_mismatch", scoring_input["evidence_integrity"]["validation_errors"])
        self.assertEqual(scoring_input["changed_files"], ["src/user/types.nico"])

    def test_run_tool_use_clean_end_turn_at_max_turn_is_not_exhausted(self) -> None:
        responses = [FakeResponse("tool_use") for _ in range(run_experiment.MAX_TURNS - 1)]
        responses.append(FakeResponse("end_turn", [FakeTextBlock("Summary:\n- done")]))
        result = run_experiment.run_tool_use(
            FakeClient(responses),
            system="system",
            task_prompt="task",
            task="E1",
            condition="C",
            run_num=1,
            dry_run=False,
            manual_tokens_per_turn=0,
            model="fake",
            provider="anthropic",
        )

        self.assertEqual(result["turns"], run_experiment.MAX_TURNS)
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["termination_status"], "clean_end_turn")

    def test_run_tool_use_marks_max_turns_exhausted(self) -> None:
        result = run_experiment.run_tool_use(
            FakeClient([FakeResponse("tool_use") for _ in range(run_experiment.MAX_TURNS)]),
            system="system",
            task_prompt="task",
            task="E1",
            condition="C",
            run_num=1,
            dry_run=False,
            manual_tokens_per_turn=0,
            model="fake",
            provider="anthropic",
        )

        self.assertEqual(result["turns"], run_experiment.MAX_TURNS)
        self.assertEqual(result["stop_reason"], "tool_use")
        self.assertEqual(result["termination_status"], "max_turns_exhausted")


if __name__ == "__main__":
    unittest.main()
