import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nico_sections import NicoSectionError, extract_nico_section


SAMPLE = r'''
module demo.module {
  spec {
    intent "literal braces { should not count }"
    interface {
      fn demo() -> ()
    }
  }

  checks {
    examples {
      example demo {
        let value = "example { brace }";
      }
    }
  }

  implementation rust {
    pub fn demo() {
      let text = format!("{:?}", "{ not a block }");
      // A line comment with { and } should be ignored.
      /*
       * A block comment with { and } should be ignored too.
       */
      let raw = r#"raw { string }"#;
      let ch = '{';
    }
  }
}
'''


class NicoSectionTest(unittest.TestCase):
    def test_extracts_surface_checks_and_implementation(self) -> None:
        surface = extract_nico_section(SAMPLE, "surface")
        checks = extract_nico_section(SAMPLE, "checks")
        implementation = extract_nico_section(SAMPLE, "implementation")

        self.assertTrue(surface.startswith("spec {"))
        self.assertIn("fn demo() -> ()", surface)
        self.assertTrue(checks.startswith("checks {"))
        self.assertIn("example demo", checks)
        self.assertTrue(implementation.startswith("implementation rust {"))
        self.assertIn('format!("{:?}"', implementation)
        self.assertIn("let ch = '{';", implementation)
        self.assertTrue(implementation.endswith("}"))

    def test_reports_unknown_or_missing_sections(self) -> None:
        with self.assertRaises(NicoSectionError):
            extract_nico_section(SAMPLE, "unknown")
        with self.assertRaises(NicoSectionError):
            extract_nico_section("module x { spec { } }", "implementation")


if __name__ == "__main__":
    unittest.main()
