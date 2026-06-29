import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_experiment


REMOVED_E1_OP = "update_user_profile" + "_timestamp_surface"
REMOVED_QUERY_HINT = "preferred" + "_op"


class PromptProtocolTest(unittest.TestCase):
    def test_condition_c_task_prompt_appends_protocol_override(self) -> None:
        prompt = run_experiment.load_task_prompt_v3("E1", "C")

        self.assertIn("--- Condition C Protocol Override ---", prompt)
        self.assertIn("use `edit_nico` to apply every source change", prompt)
        self.assertIn("`insert_interface_item`", prompt)
        self.assertIn("`replace_interface_item`", prompt)
        self.assertIn("`update_interface_function_effects`", prompt)
        self.assertIn("`replace_implementation_function`", prompt)
        self.assertIn("`update_module_imports`", prompt)
        self.assertIn("module-level `imports [...]`", prompt)
        self.assertIn("source_edit_plan", prompt)
        self.assertIn("DB source", prompt)
        self.assertIn("source_effect_update_plan", prompt)
        self.assertIn("required_edit=true", prompt)
        self.assertIn("required_edit=false", prompt)
        self.assertIn("no_op_edit=forbidden", prompt)
        self.assertIn("Do not run routine dry-run previews", prompt)
        self.assertIn("stale example or call site", prompt)
        self.assertIn("only forbid edits for that row's effect action", prompt)
        self.assertIn("do not invent or assume an anchor", prompt)
        self.assertIn("exact text copied from the narrow source section", prompt)
        self.assertIn("completion gates have been satisfied", prompt)
        self.assertIn("Do not treat", prompt)
        self.assertIn("DB `propagated_effects`", prompt)
        self.assertIn("Stop rule:", prompt)
        self.assertIn("comment/doc-only edits", prompt)
        self.assertIn("formatting cleanup", prompt)
        self.assertIn("dry-run previews", prompt)
        self.assertIn("Do not present complete updated module contents", prompt)
        self.assertIn("Final answer format:", prompt)
        self.assertNotIn("For E1/E5-style", prompt)
        self.assertNotIn("op=update_", prompt)
        self.assertNotIn(REMOVED_E1_OP, prompt)
        self.assertNotIn(REMOVED_QUERY_HINT, prompt)

        tools = run_experiment.get_tools("C")
        edit_tool = next(tool for tool in tools if tool["name"] == "edit_nico")
        op_enum = edit_tool["input_schema"]["properties"]["edits"]["items"]["properties"]["op"]["enum"]
        self.assertNotIn(REMOVED_E1_OP, op_enum)

    def test_condition_a_and_d_task_prompts_do_not_append_c_protocol(self) -> None:
        for condition in ("A", "D"):
            with self.subTest(condition=condition):
                prompt = run_experiment.load_task_prompt_v3("E1", condition)

                self.assertNotIn("--- Condition C Protocol Override ---", prompt)
                self.assertNotIn("Use `edit_nico` to apply every source change", prompt)
                self.assertIn("For each module that requires changes, output its complete updated content.", prompt)

    def test_deepseek_provider_defaults_and_request_options(self) -> None:
        self.assertEqual(
            run_experiment.effective_model("deepseek", None),
            "deepseek-v4-flash",
        )
        self.assertEqual(
            run_experiment.provider_request_options("deepseek"),
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(run_experiment.api_format("deepseek"), "anthropic")

    def test_anthropic_provider_defaults_and_request_options(self) -> None:
        self.assertEqual(
            run_experiment.effective_model("anthropic", None),
            "claude-sonnet-4-5",
        )
        self.assertEqual(run_experiment.provider_request_options("anthropic"), {})
        self.assertEqual(run_experiment.api_format("anthropic"), "anthropic")


if __name__ == "__main__":
    unittest.main()
