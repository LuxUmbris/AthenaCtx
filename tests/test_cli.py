import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "build" / "athenactx.exe"
if not BIN.is_file():
    BIN = ROOT / "build" / "athenactx"
FIXTURE = ROOT / "tests" / "fixture.shaft"
MALFORMED = ROOT / "tests" / "malformed.shaft"
INVALID = ROOT / "tests" / "invalid.shaft"
PYTHON_FIXTURE = ROOT / "tests" / "fixture.py"
PYTHON_MALFORMED = ROOT / "tests" / "malformed.py"
RUST_FIXTURE = ROOT / "tests" / "fixture.rs"
JS_FIXTURE = ROOT / "tests" / "fixture.js"
BASH_FIXTURE = ROOT / "tests" / "fixture.sh"
C_FIXTURE = ROOT / "tests" / "fixture.c"
ZIG_FIXTURE = ROOT / "tests" / "fixture.zig"


class AthenaCtxCliTests(unittest.TestCase):
    def run_cli(self, *args):
        command = [str(BIN)]
        compiler = shutil.which("shaftc")
        if compiler:
            command.extend(["--compiler", compiler])
        command.extend(map(str, args))
        return subprocess.run(command, text=True, capture_output=True, cwd=ROOT)

    def test_shaft_capabilities_does_not_require_python_backend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            command = [str(BIN)]
            compiler = shutil.which("shaftc")
            if compiler:
                command.extend(["--compiler", compiler])
            command.extend(["capabilities", "shaft"])
            result = subprocess.run(command, text=True, capture_output=True, cwd=temp_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "calls": "dedicated analyzer",
                "declaration_extraction": "dedicated analyzer",
                "declaration_tags": False,
                "diagnostics": "Shaft compiler and structural diagnostics",
                "language": "shaft",
                "parser": True,
                "references": "syntactic declarations and calls",
                "syntax_inventory": False,
            },
        )

    def test_shaft_scan_reports_members_errors_and_hides_bodies(self):
        result = self.run_cli("scan", FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        symbols = data["symbols"]
        user = next(s for s in symbols if s["kind"] == "struct" and s["name"] == "User")
        self.assertEqual(user["fields"][0]["name"], "id")
        color = next(s for s in symbols if s["kind"] == "enum" and s["name"] == "Color")
        self.assertEqual([m["name"] for m in color["members"]], ["Red", "Green", "Blue"])
        choice = next(s for s in symbols if s["kind"] == "union" and s["name"] == "Choice")
        self.assertEqual([m["name"] for m in choice["members"]], ["Number", "Label"])
        self.assertEqual(data["diagnostics"], [])
        self.assertNotIn("generated", result.stdout)
        self.assertNotIn("load_user(id);", result.stdout)

    def test_tree_sitter_scans_python_declarations_calls_and_errors(self):
        result = self.run_cli("scan", PYTHON_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "python")
        symbols = data["symbols"]
        self.assertTrue(any(s["kind"] == "class" and s["name"] == "Widget" for s in symbols))
        render = next(s for s in symbols if s["kind"] == "function" and s["name"] == "render")
        self.assertTrue(any(c["name"] == "helper" and c["caller"] == "render" for c in data["calls"]))
        self.assertEqual(data["diagnostics"], [])
        malformed = self.run_cli("scan", PYTHON_MALFORMED)
        self.assertEqual(malformed.returncode, 0, malformed.stderr)
        errors = json.loads(malformed.stdout)["diagnostics"]
        self.assertTrue(errors)
        self.assertTrue(any(d["severity"] == "error" for d in errors))

    def test_tree_sitter_indexes_rust_members_and_union_fields(self):
        result = self.run_cli("scan", RUST_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "rust")
        symbols = data["symbols"]
        color_members = [s for s in symbols if s["kind"] == "enum_member" and s.get("container") == "Color"]
        self.assertEqual([s["name"] for s in color_members], ["Red", "Green"])
        raw_fields = [s for s in symbols if s["kind"] == "field" and s.get("container") == "Raw"]
        self.assertEqual([s["name"] for s in raw_fields], ["number", "bits"])
        self.assertEqual(len([s for s in symbols if s["kind"] == "function" and s["name"] == "sum"]), 1)
        self.assertTrue(any(c["name"] == "helper" and c["caller"] == "sum" for c in data["calls"]))
        self.assertEqual(data["diagnostics"], [])

    def test_tree_sitter_scans_javascript(self):
        result = self.run_cli("scan", JS_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "javascript")
        symbols = data["symbols"]
        self.assertTrue(any(s["kind"] == "function" and s["name"] == "add" for s in symbols))
        self.assertTrue(any(s["kind"] == "class" and s["name"] == "Box" for s in symbols))
        self.assertTrue(any(c["name"] == "add" and c["caller"] == "value" for c in data["calls"]))
        self.assertEqual(data["diagnostics"], [])

    def test_tree_sitter_scans_shell_without_tag_query(self):
        result = self.run_cli("scan", BASH_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "bash")
        self.assertTrue(any(s["kind"] == "function" and s["name"] == "greet" for s in data["symbols"]))
        self.assertTrue(any(c["name"] == "printf" and c["caller"] == "greet" for c in data["calls"]))
        self.assertTrue(any(e["caller"] == "greet" for e in data["effects"]))

    def test_tree_sitter_loads_c_grammar_on_demand(self):
        result = self.run_cli("scan", C_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "c")
        self.assertTrue(any(s["kind"] == "enum_member" and s["container"] == "Color" and s["name"] == "RED" for s in data["symbols"]))
        self.assertTrue(any(s["kind"] == "field" and s["container"] == "Value" and s["name"] == "number" for s in data["symbols"]))
        self.assertEqual(data["diagnostics"], [])

    def test_tree_sitter_scans_zig_function_declarations(self):
        result = self.run_cli("scan", ZIG_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "zig")
        self.assertTrue(any(s["kind"] == "function" and s["name"] == "answer" for s in data["symbols"]))
        self.assertEqual(data["diagnostics"], [])

    def test_index_includes_c_and_zig_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shutil.copy(C_FIXTURE, root / "sample.c")
            shutil.copy(ZIG_FIXTURE, root / "sample.zig")
            result = self.run_cli("index", root)

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["files"], ["sample.c", "sample.zig"])
        self.assertTrue(any(s["file"] == "sample.zig" and s["name"] == "answer" for s in data["symbols"]))
        self.assertTrue(any(s["file"] == "sample.c" and s["name"] == "Color" for s in data["symbols"]))
        self.assertEqual(data["diagnostics"], [])

    def test_tree_sitter_lists_supported_grammars_and_returns_python_body(self):
        result = self.run_cli("languages")
        self.assertEqual(result.returncode, 0, result.stderr)
        languages = result.stdout.splitlines()
        self.assertGreaterEqual(len(languages), 300)
        self.assertTrue({"python", "javascript", "rust", "zig", "c"}.issubset(languages))
        body = self.run_cli("implementation", PYTHON_FIXTURE, "def render(self) -> str:")
        self.assertEqual(body.returncode, 0, body.stderr)
        self.assertIn("return helper(self.value)", body.stdout)
        rust_body = self.run_cli("implementation", RUST_FIXTURE, "fn helper(value: i32) -> i32")
        self.assertEqual(rust_body.returncode, 0, rust_body.stderr)
        self.assertIn("value", rust_body.stdout)

    def test_vendored_tree_sitter_parses_every_manifest_grammar(self):
        env = os.environ.copy()
        vendor = str(ROOT / "vendor")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [vendor, env.get("PYTHONPATH", "")]))
        script = """
import sys
import tree_sitter_language_pack as pack

languages = pack.manifest_languages()
failures = []
for language in languages:
    try:
        pack.get_parser(language).parse(b"")
    except Exception as error:
        failures.append(f"{language}: {error}")
print(f"{len(languages)} grammars, {len(failures)} failures")
if failures:
    print("\\n".join(failures), file=sys.stderr)
    raise SystemExit(1)
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            text=True,
            capture_output=True,
            cwd=ROOT,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("372 grammars, 0 failures", result.stdout)

    def test_implementation_request_returns_exact_body(self):
        result = self.run_cli("implementation", FIXTURE, "def async lookup(u64 id) -> u64 result")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{\n    reserve u64 value = load_user(id);\n    tunnel value -> u64 result;\n}\n")

    def test_implementation_requires_exact_signature(self):
        result = self.run_cli("implementation", FIXTURE, "def async lookup(u64 id)")
        self.assertEqual(result.returncode, 2)
        self.assertIn("No definition matched", result.stderr)

    def test_prompt_is_available(self):
        result = self.run_cli("prompt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Do not read implementation bodies by default", result.stdout)

    def test_malformed_shaft_braces_are_rejected(self):
        result = self.run_cli("scan", MALFORMED)
        self.assertNotEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "shaft")
        self.assertTrue(any(d["severity"] == "error" for d in data["diagnostics"]))

    def test_shaft_compiler_diagnostics_are_reported(self):
        result = self.run_cli("scan", INVALID)
        self.assertNotEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertTrue(data["diagnostics"])
        self.assertTrue(any("return" in d["message"].lower() for d in data["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
