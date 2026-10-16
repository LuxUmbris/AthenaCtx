import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "build" / "athenactx.exe"
if not CLI.is_file():
    CLI = ROOT / "build" / "athenactx"
sys.path[:0] = [str(ROOT / "vendor"), str(ROOT / "tools")]
import project_cli


class ProjectCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "a.py").write_text("def greet(name):\n    return name\n")
        (self.root / "b.py").write_text("from a import greet\n\ndef run():\n    return greet('Ada')\n")
        (self.root / "sample.shaft").write_text((ROOT / "tests" / "fixture.shaft").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        command = [str(CLI)]
        compiler = shutil.which("shaftc")
        if compiler:
            command.extend(["--compiler", compiler])
        command.extend(map(str, args))
        return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)

    def test_scan_file_records_missing_scanner_json_as_diagnostic(self):
        path = self.root / "a.py"
        result = subprocess.CompletedProcess([], -11, stdout="", stderr="")
        with patch.object(project_cli.subprocess, "run", return_value=result):
            data = project_cli.scan_file(path, "python")
        self.assertEqual(data["language"], "python")
        self.assertTrue(data["diagnostics"])
        self.assertIn("scanner returned no JSON", data["diagnostics"][0]["message"])

    def test_index_links_workspace_definitions_imports_references_and_cache(self):
        first = self.run_cli("index", self.root)
        self.assertEqual(first.returncode, 0, first.stderr)
        data = json.loads(first.stdout)
        self.assertEqual(len(data["files"]), 3)
        self.assertIn("sample.shaft", data["files"])
        self.assertTrue(any(r["name"] == "greet" for r in data["references"]))
        self.assertTrue(any(link["name"] == "greet" for link in data["imports"]))
        ref = self.run_cli("references", "greet", "--root", self.root)
        self.assertEqual(ref.returncode, 0, ref.stderr)
        refs = json.loads(ref.stdout)
        self.assertTrue(any(r["role"] == "definition" for r in refs["references"]))
        self.assertTrue(any(r["role"] == "import" for r in refs["references"]))
        self.assertTrue(any(r["role"] == "call" for r in refs["references"]))
        again = json.loads(self.run_cli("index", self.root).stdout)
        self.assertGreaterEqual(again["cache"]["hits"], 3)

    def test_scan_imports_follows_local_python_imports_recursively_and_handles_cycles(self):
        (self.root / "entry.py").write_text("from pkg.first import value\n")
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "__init__.py").write_text("")
        (self.root / "pkg" / "first.py").write_text(
            "from pkg.second import answer\nvalue = answer\n"
        )
        (self.root / "pkg" / "second.py").write_text("import pkg.first\nanswer = 42\n")
        (self.root / "unrelated.py").write_text("raise RuntimeError('not part of the closure')\n")

        result = self.run_cli("scan", self.root / "entry.py", "--imports", "--root", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(set(data["files"]), {
            "entry.py", "pkg/__init__.py", "pkg/first.py", "pkg/second.py"
        })
        self.assertEqual(data["cache"]["misses"], 4)
        again_result = self.run_cli("scan", self.root / "entry.py", "--imports", "--root", self.root)
        self.assertEqual(again_result.returncode, 0, again_result.stderr)
        again = json.loads(again_result.stdout)
        self.assertEqual(again["cache"]["hits"], 4)

    def test_scan_imports_supports_cross_language_resolvers(self):
        cases = [
            ("web/main.js", "import { value } from './lib.js';\n", {
                "web/lib.js": "export { answer } from './answer.js';\n",
                "web/answer.js": "export const answer = 42;\n",
                "web/unused.js": "export const unused = true;\n",
            }, {"web/main.js", "web/lib.js", "web/answer.js"}),
            ("rust/main.rs", "mod alpha;\n", {
                "rust/alpha.rs": "mod beta;\n",
                "rust/beta.rs": "pub fn value() {}\n",
                "rust/unused.rs": "pub fn unused() {}\n",
            }, {"rust/main.rs", "rust/alpha.rs", "rust/beta.rs"}),
            ("native/main.c", '#include "local.h"\n#include <stdio.h>\n', {
                "native/local.h": '#include "nested.h"\n',
                "native/nested.h": "int answer;\n",
                "native/unused.h": "int unused;\n",
            }, {"native/main.c", "native/local.h", "native/nested.h"}),
            ("shaft/main.shaft", 'import "part.shaft";\n', {
                "shaft/part.shaft": "def answer() -> i32 { 42 }\n",
                "shaft/unused.shaft": "def unused() -> i32 { 0 }\n",
            }, {"shaft/main.shaft", "shaft/part.shaft"}),
        ]
        for entry, source, dependencies, expected in cases:
            with self.subTest(entry=entry):
                (self.root / entry).parent.mkdir(parents=True, exist_ok=True)
                (self.root / entry).write_text(source)
                for rel, content in dependencies.items():
                    path = self.root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                result = self.run_cli("scan", self.root / entry, "--imports", "--root", self.root)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(set(json.loads(result.stdout)["files"]), expected)

    def test_scan_imports_respects_depth_limit(self):
        (self.root / "entry.py").write_text("import child\n")
        (self.root / "child.py").write_text("value = 1\n")
        result = self.run_cli("scan", self.root / "entry.py", "--imports",
                              "--root", self.root, "--max-depth", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["files"], ["entry.py"])
        self.assertTrue(data["truncated"])

    def test_scan_dot_routes_to_workspace_index(self):
        result = self.run_cli("scan", ".")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(Path(data["root"]), ROOT)
        self.assertIn("src/main.shaft", data["files"])

    def test_query_filters_symbols_and_calls(self):
        self.run_cli("index", self.root)
        result = self.run_cli("query", "--root", self.root, "--kind", "function", "--name", "greet")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual([item["name"] for item in data["symbols"]], ["greet"])
        calls = self.run_cli("query", "--root", self.root, "--caller", "run",
                             "--callee", "greet", "--file", "b.py")
        self.assertEqual(calls.returncode, 0, calls.stderr)
        self.assertEqual(len(json.loads(calls.stdout)["calls"]), 1)

    def test_rename_preview_then_apply_updates_references(self):
        self.run_cli("index", self.root)
        preview = self.run_cli("rename", "greet", "welcome", "--root", self.root)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("syntactic", json.loads(preview.stdout)["confidence"])
        self.assertIn("greet", (self.root / "a.py").read_text())
        applied = self.run_cli("rename", "greet", "welcome", "--root", self.root, "--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("welcome", (self.root / "a.py").read_text())
        self.assertIn("welcome", (self.root / "b.py").read_text())

    def test_capabilities_report_parser_and_extraction_confidence(self):
        result = self.run_cli("capabilities", "python")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["language"], "python")
        self.assertTrue(data["parser"])
        shaft = self.run_cli("capabilities", "shaft")
        self.assertEqual(shaft.returncode, 0, shaft.stderr)
        self.assertEqual(json.loads(shaft.stdout)["declaration_extraction"], "dedicated analyzer")
        self.assertIn("declaration_extraction", data)
        self.assertIn(data["declaration_extraction"], {"tagged", "heuristic", "unavailable"})

    def test_diff_summarizes_changed_symbols_and_callers(self):
        self.run_cli("index", self.root)
        (self.root / "a.py").write_text("def greet(name):\n    return name.title()\n")
        result = self.run_cli("diff", "--root", self.root, "--base", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("changed_symbols", data)
        self.assertIn("affected_files", data)

    def test_lsp_supports_initialize_and_workspace_symbols(self):
        messages = [
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"rootUri":self.root.as_uri()}},
            {"jsonrpc":"2.0","id":2,"method":"workspace/symbol","params":{"query":"greet"}},
            {"jsonrpc":"2.0","id":3,"method":"textDocument/definition","params":{"textDocument":{"uri":(self.root / "b.py").as_uri()},"position":{"line":3,"character":13}}},
            {"jsonrpc":"2.0","id":4,"method":"textDocument/references","params":{"textDocument":{"uri":(self.root / "b.py").as_uri()},"position":{"line":3,"character":13},"context":{"includeDeclaration":True}}},
            {"jsonrpc":"2.0","id":5,"method":"textDocument/rename","params":{"textDocument":{"uri":(self.root / "b.py").as_uri()},"position":{"line":3,"character":13},"newName":"welcome"}},
            {"jsonrpc":"2.0","id":6,"method":"shutdown","params":{}},
        ]
        frame = "".join(
            f"Content-Length: {len(payload := json.dumps(message).encode())}\r\n\r\n{payload.decode()}"
            for message in messages
        )
        result = subprocess.run([str(CLI), "lsp"], cwd=ROOT, input=frame, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workspaceSymbolProvider", result.stdout)
        self.assertIn("definitionProvider", result.stdout)
        self.assertIn('"name":"greet"', result.stdout)
        self.assertIn((self.root / "a.py").as_uri(), result.stdout)
        self.assertIn('"changes"', result.stdout)

    def test_lsp_reindexes_live_open_documents(self):
        uri = (self.root / "a.py").as_uri()
        messages = [
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"rootUri":self.root.as_uri()}},
            {"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{"uri":uri,"languageId":"python","version":1,"text":"def greeting(name):" + chr(10) + "    return name" + chr(10)}}},
            {"jsonrpc":"2.0","id":2,"method":"workspace/symbol","params":{"query":"greeting"}},
            {"jsonrpc":"2.0","id":3,"method":"shutdown","params":{}},
        ]
        frame = "".join(
            f"Content-Length: {len(payload := json.dumps(message).encode())}\r\n\r\n{payload.decode()}"
            for message in messages
        )
        result = subprocess.run([str(CLI), "lsp", "--root", str(self.root)], cwd=ROOT, input=frame, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"name":"greeting"', result.stdout)

    def test_watch_emits_incremental_index_event(self):
        result = self.run_cli("watch", "--root", self.root, "--once")
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads(result.stdout)
        self.assertEqual(event["event"], "index_updated")
        self.assertEqual(event["cache"]["misses"], 3)


if __name__ == "__main__":
    unittest.main()
