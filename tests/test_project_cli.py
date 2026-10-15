import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "athenactx"


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
        return subprocess.run([str(CLI), *map(str, args)], cwd=ROOT, text=True, capture_output=True)

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
