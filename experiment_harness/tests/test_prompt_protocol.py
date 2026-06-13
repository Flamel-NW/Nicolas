import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_experiment


class PromptProtocolTest(unittest.TestCase):
    def test_condition_c_task_prompt_appends_protocol_override(self) -> None:
        prompt = run_experiment.load_task_prompt_v3("E1", "C")

        self.assertIn("--- Condition C Protocol Override ---", prompt)
        self.assertIn("Use `edit_nico` to apply every source change", prompt)
        self.assertIn("Do not present complete updated module contents", prompt)
        self.assertIn("Final answer format:", prompt)

    def test_condition_a_and_d_task_prompts_do_not_append_c_protocol(self) -> None:
        for condition in ("A", "D"):
            with self.subTest(condition=condition):
                prompt = run_experiment.load_task_prompt_v3("E1", condition)

                self.assertNotIn("--- Condition C Protocol Override ---", prompt)
                self.assertNotIn("Use `edit_nico` to apply every source change", prompt)
                self.assertIn("For each module that requires changes, output its complete updated content.", prompt)


if __name__ == "__main__":
    unittest.main()
