#!/usr/bin/env python3
"""Workspace indexing and editor features built on the existing single-file scanner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import tree_sitter_language_pack as pack
from import_resolvers import resolver_for

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCANNER = ROOT / "build" / "athenactx.exe"
if not DEFAULT_SCANNER.is_file():
    DEFAULT_SCANNER = ROOT / "build" / "athenactx"
SCANNER = Path(os.environ.get("ATHENACTX_BIN", DEFAULT_SCANNER))
SKIP_DIRS = {".git", ".athenactx", "build", "dist", "node_modules", "vendor", ".venv", "venv", "__pycache__"}
IDENTIFIER_TYPES = {"identifier", "type_identifier", "field_identifier", "property_identifier", "word"}


def json_out(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def scan_file(path: Path, language: str | None = None):
    args = [str(SCANNER)]
    compiler = shutil.which("shaftc")
    if compiler:
        args.extend(["--compiler", compiler])
    args.extend(["scan", str(path)])
    if language:
        args.append(language)
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detected = language or pack.detect_language_from_path(str(path)) or "unknown"
        detail = result.stderr.strip() or f"scanner returned no JSON ({exc})"
        data = {
            "file": str(path), "language": detected, "symbols": [], "calls": [],
            "effects": [], "diagnostics": [{"severity": "error", "message": detail,
            "line": 1, "column": 1}], "syntax": [],
        }
        return data
    if result.returncode not in (0, 2):
        data.setdefault("diagnostics", []).append({
            "severity": "error", "message": result.stderr.strip() or f"scanner exited with status {result.returncode}",
            "line": 1, "column": 1,
        })
    if language and data.get("language") == "unknown":
        data.setdefault("diagnostics", []).append({
            "severity": "error", "message": f"scanner returned unknown language for requested {language}",
            "line": 1, "column": 1,
        })
    data["file"] = str(path)
    return data


def supported_files(root: Path):
    files = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            path = Path(current) / name
            language = "shaft" if path.suffix == ".shaft" else pack.detect_language_from_path(str(path))
            if language == "shaft" or (language and pack.has_language(language)):
                files.append((path, language))
    return files


def cache_path(root: Path):
    return root / ".athenactx" / "index.json"


def fingerprint(path: Path):
    content = path.read_bytes()
    stat = path.stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size,
            "sha256": hashlib.sha256(content).hexdigest()}


def make_references(scan, source):
    refs = []
    syntax = scan.get("syntax", [])
    spans = set()
    for node in syntax:
        if node.get("type") not in IDENTIFIER_TYPES:
            continue
        start, end = node.get("start_byte"), node.get("end_byte")
        if start is None or end is None or (start, end) in spans:
            continue
        spans.add((start, end))
        try:
            name = source.encode("utf-8")[start:end].decode("utf-8")
        except (UnicodeDecodeError, IndexError):
            continue
        if not name:
            continue
        line = node.get("line", 1)
        role = "reference"
        if any(s.get("name") == name and s.get("line") == line for s in scan.get("symbols", [])):
            role = "definition"
        elif any(c.get("name") == name and c.get("line") == line for c in scan.get("calls", [])):
            role = "call"
        elif any("import" in n.get("type", "") and
                 n.get("start_byte", 1) <= start < n.get("end_byte", 0) for n in syntax):
            role = "import"
        refs.append({"name": name, "file": scan["file"], "line": line,
                     "column": node.get("column", 1), "start_byte": start,
                     "end_byte": end, "role": role, "confidence": "syntactic"})
    if not syntax:
        lines = source.splitlines(keepends=True)
        for symbol in scan.get("symbols", []):
            line = symbol.get("line", 1)
            text = lines[line - 1] if line <= len(lines) else ""
            match = re.search(r"(?<![\w])" + re.escape(symbol["name"]) + r"(?![\w])", text)
            if match:
                refs.append({"name": symbol["name"], "file": scan["file"], "line": line,
                             "column": match.start() + 1, "start_byte": None,
                             "end_byte": None, "role": "definition", "confidence": "line-level"})
        for call in scan.get("calls", []):
            line = call.get("line", 1)
            text = lines[line - 1] if line <= len(lines) else ""
            match = re.search(r"(?<![\w])" + re.escape(call["name"]) + r"(?![\w])", text)
            if match:
                refs.append({"name": call["name"], "file": scan["file"], "line": line,
                             "column": match.start() + 1, "start_byte": None,
                             "end_byte": None, "role": "call", "confidence": "line-level"})
    return refs


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cache_file = cache_path(self.root)
        try:
            self.cache = json.loads(self.cache_file.read_text())
        except (OSError, json.JSONDecodeError):
            self.cache = {"version": 1, "files": {}}
        self.previous_files = self.cache.get("files", {})
        self.changed = []
        self.files = []
        self.scans = {}
        self.sources = {}
        self.references = []
        self.imports = []
        self.cache_hits = 0
        self.cache_misses = 0

    def build(self, force=False):
        updated = {}
        discovered = supported_files(self.root)
        known = set()
        for path, language in discovered:
            rel = path.relative_to(self.root).as_posix()
            known.add(rel)
            mark = fingerprint(path)
            old = self.previous_files.get(rel)
            if old and old.get("fingerprint") == mark and not force:
                scan = old["scan"]
                self.cache_hits += 1
            else:
                scan = scan_file(path, language)
                self.cache_misses += 1
                self.changed.append(rel)
            updated[rel] = {"fingerprint": mark, "scan": scan}
            self.files.append(rel)
            self.scans[rel] = scan
            source = path.read_text(encoding="utf-8", errors="replace")
            self.sources[rel] = source
            self.references.extend(make_references(scan, source))
        removed = sorted(set(self.previous_files) - known)
        self.changed.extend(removed)
        self.cache = {"version": 1, "files": updated}
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.cache_file)
        self._link_imports()
        return self

    def _link_imports(self):
        self.imports = []
        defs = {}
        for rel, scan in self.scans.items():
            for symbol in scan.get("symbols", []):
                defs.setdefault(symbol.get("name", ""), []).append(rel)
        for rel, scan in self.scans.items():
            source = self.sources[rel]
            syntax = scan.get("syntax", [])
            nodes = [node for node in syntax if "import" in node.get("type", "")]
            for node in nodes:
                start, end = node.get("start_byte", 0), node.get("end_byte", 0)
                raw = source.encode("utf-8")[start:end].decode("utf-8", errors="replace")
                identifiers = re.findall(r"[A-Za-z_$][\w$]*", raw)
                for name in identifiers:
                    targets = [target for target in defs.get(name, []) if target != rel]
                    self.imports.append({"name": name, "file": rel,
                                         "line": node.get("line", 1),
                                         "targets": sorted(set(targets)),
                                         "resolved": bool(targets),
                                         "confidence": "syntactic"})

    def query_references(self, name):
        return [ref for ref in self.references if ref["name"] == name]

    def definitions(self, name):
        return [ref for ref in self.query_references(name) if ref["role"] == "definition"]

    def update_document(self, rel, source):
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            return
        language = "shaft" if path.suffix == ".shaft" else pack.detect_language_from_path(str(path))
        if not language:
            return
        with tempfile.NamedTemporaryFile("w", suffix=path.suffix, encoding="utf-8", delete=False) as stream:
            stream.write(source)
            temporary = Path(stream.name)
        try:
            scan = scan_file(temporary, language)
        finally:
            temporary.unlink(missing_ok=True)
        scan["file"] = str(path)
        self.files = sorted(set(self.files) | {rel})
        self.scans[rel] = scan
        self.sources[rel] = source
        self.references = [ref for ref in self.references if Path(ref["file"]).resolve() != path]
        self.references.extend(make_references(scan, source))
        self._link_imports()


def scan_imports_command(args):
    root = Path(args.root).resolve() if args.root else Path(args.entry).resolve().parent
    entry = Path(args.entry).resolve()
    if not entry.is_relative_to(root) or not entry.is_file():
        raise ValueError("entry file must exist inside the selected root")

    cache_file = cache_path(root)
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {"version": 1, "files": {}}
    cached_files = cache.get("files", {})
    updated_files = dict(cached_files)
    scans = {}
    sources = {}
    languages = {}
    imports = []
    unresolved = []
    queue = [(entry, 0)]
    queued = {entry}
    cache_hits = 0
    cache_misses = 0
    truncated = False

    while queue:
        path, depth = queue.pop(0)
        path = path.resolve()
        queued.discard(path)
        if not path.is_relative_to(root):
            continue
        rel = path.relative_to(root).as_posix()
        if rel in scans:
            continue
        language = "shaft" if path.suffix == ".shaft" else pack.detect_language_from_path(str(path))
        if not language or (language != "shaft" and not pack.has_language(language)):
            unresolved.append({"file": rel,
                               "specifier": None, "reason": "unsupported source language"})
            continue

        mark = fingerprint(path)
        old = cached_files.get(rel)
        if old and old.get("fingerprint") == mark:
            scan = old["scan"]
            cache_hits += 1
        else:
            scan = scan_file(path, language)
            cache_misses += 1
        updated_files[rel] = {"fingerprint": mark, "scan": scan}
        scans[rel] = scan
        sources[rel] = path.read_text(encoding="utf-8", errors="replace")
        languages[rel] = language

        resolver = resolver_for(language)
        if resolver is None:
            unresolved.append({"file": rel, "specifier": None,
                               "reason": f"no import resolver for {language}"})
            continue
        for specifier in dict.fromkeys(resolver.extract(sources[rel], path, root)):
            targets = []
            for target in resolver.resolve(specifier, path, root):
                target = target.resolve()
                target_language = "shaft" if target.suffix == ".shaft" else pack.detect_language_from_path(str(target))
                if target_language and (target_language == "shaft" or pack.has_language(target_language)):
                    targets.append(target)
            if not targets:
                unresolved.append({"file": rel, "specifier": specifier,
                                   "reason": "not found inside root"})
                continue
            for target in targets:
                target_rel = target.relative_to(root).as_posix()
                imports.append({"from": rel, "specifier": specifier,
                                "to": target_rel, "resolved": True,
                                "confidence": "language resolver"})
                if target in scans or target in queued:
                    continue
                if depth >= args.max_depth or len(scans) + len(queue) >= args.max_files:
                    truncated = True
                    continue
                queue.append((target, depth + 1))
                queued.add(target)

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_file.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "files": updated_files}, ensure_ascii=False),
                    encoding="utf-8")
    temp.replace(cache_file)

    files = sorted(scans)
    json_out({
        "root": str(root),
        "entry": entry.relative_to(root).as_posix(),
        "files": files,
        "scans": [{"file": rel, "language": languages[rel],
                   "symbols": scans[rel].get("symbols", []),
                   "calls": scans[rel].get("calls", []),
                   "effects": scans[rel].get("effects", []),
                   "diagnostics": scans[rel].get("diagnostics", [])}
                  for rel in files],
        "references": [ref for rel in files
                       for ref in make_references(scans[rel], sources[rel])],
        "imports": imports,
        "unresolved_imports": unresolved,
        "cache": {"hits": cache_hits, "misses": cache_misses},
        "truncated": truncated,
    })


def index_command(args):
    root = Path(args.root).resolve()
    workspace = Workspace(root).build(force=args.force)
    json_out({"root": str(root), "files": workspace.files,
              "symbols": [{**symbol, "file": rel} for rel, scan in workspace.scans.items()
                          for symbol in scan.get("symbols", [])],
              "references": workspace.references,
              "imports": workspace.imports,
              "links": [{"name": item["name"], "from": item["file"], "to": target,
                         "line": item["line"], "confidence": item["confidence"]}
                        for item in workspace.imports for target in item["targets"]],
              "diagnostics": [{**diag, "file": rel} for rel, scan in workspace.scans.items()
                              for diag in scan.get("diagnostics", [])],
              "changed_files": sorted(workspace.changed),
              "cache": {"hits": workspace.cache_hits, "misses": workspace.cache_misses}})


def references_command(args):
    workspace = Workspace(Path(args.root)).build()
    refs = workspace.query_references(args.symbol)
    json_out({"symbol": args.symbol, "confidence": "syntactic", "references": refs})


def query_command(args):
    workspace = Workspace(Path(args.root)).build()
    symbols, calls, diagnostics = [], [], []
    for rel, scan in workspace.scans.items():
        if args.file and args.file not in rel:
            continue
        symbols.extend({**s, "file": rel} for s in scan.get("symbols", [])
                       if (not args.kind or s.get("kind") == args.kind)
                       and (not args.name or args.name in s.get("name", "")))
        calls.extend({**c, "file": rel} for c in scan.get("calls", [])
                     if (not args.caller or c.get("caller") == args.caller)
                     and (not args.callee or c.get("name") == args.callee))
        diagnostics.extend({**d, "file": rel} for d in scan.get("diagnostics", []))
    json_out({"symbols": symbols, "calls": calls,
              "diagnostics": diagnostics if args.diagnostics else []})


def rename_command(args):
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", args.new_name):
        raise ValueError("new name must be a plain identifier")
    workspace = Workspace(Path(args.root)).build()
    refs = workspace.query_references(args.old_name)
    edits = {}
    for ref in refs:
        rel = Path(ref["file"]).relative_to(workspace.root).as_posix()
        if ref["start_byte"] is not None:
            edits.setdefault(rel, []).append((ref["start_byte"], ref["end_byte"]))
        else:
            line = workspace.sources[rel].splitlines(keepends=True)[ref["line"] - 1]
            match = re.search(r"(?<![\w])" + re.escape(args.old_name) + r"(?![\w])", line)
            if match:
                offset = sum(len(item.encode("utf-8")) for item in workspace.sources[rel].splitlines(keepends=True)[:ref["line"] - 1])
                edits.setdefault(rel, []).append((offset + len(line[:match.start()].encode("utf-8")),
                                                   offset + len(line[:match.end()].encode("utf-8"))))
    changes = []
    for rel, spans in edits.items():
        raw = (workspace.root / rel).read_bytes()
        for start, end in sorted(set(spans), reverse=True):
            if raw[start:end].decode("utf-8", errors="replace") == args.old_name:
                raw = raw[:start] + args.new_name.encode("utf-8") + raw[end:]
        changes.append({"file": rel, "references": len(set(spans))})
        if args.apply:
            (workspace.root / rel).write_bytes(raw)
    json_out({"old_name": args.old_name, "new_name": args.new_name,
              "confidence": "syntactic; review edits before applying",
              "applied": args.apply, "changes": changes})


def capabilities_command(args):
    if args.language == "shaft":
        json_out({"language": "shaft", "parser": True, "syntax_inventory": False,
                  "declaration_extraction": "dedicated analyzer", "declaration_tags": False,
                  "calls": "dedicated analyzer", "references": "syntactic declarations and calls",
                  "diagnostics": "Shaft compiler and structural diagnostics"})
        return
    langs = pack.manifest_languages()
    if args.language not in langs:
        raise ValueError(f"unsupported language: {args.language}")
    tags = bool(pack.get_tags_query(args.language))
    json_out({"language": args.language, "parser": True,
              "syntax_inventory": True,
              "declaration_extraction": "tagged" if tags else "heuristic",
              "declaration_tags": tags,
              "calls": "syntactic heuristic",
              "references": "syntactic identifier matches",
              "diagnostics": "parser diagnostics; no semantic guarantee"})


def diff_command(args):
    root = Path(args.root).resolve()
    workspace = Workspace(root).build()
    changed = set(workspace.changed)
    git = subprocess.run(["git", "-C", str(root), "diff", "--name-only", args.base],
                         text=True, capture_output=True)
    if git.returncode == 0:
        changed.update(line.strip() for line in git.stdout.splitlines() if line.strip())
    symbols = []
    callers = []
    diagnostics = []
    for rel in sorted(changed):
        scan = workspace.scans.get(rel)
        if not scan:
            continue
        symbols.extend({**symbol, "file": rel} for symbol in scan.get("symbols", []))
        diagnostics.extend({**diag, "file": rel} for diag in scan.get("diagnostics", []))
        names = {symbol.get("name") for symbol in scan.get("symbols", [])}
        callers.extend({**call, "file": rel} for other, result in workspace.scans.items()
                       for call in result.get("calls", []) if call.get("name") in names)
    json_out({"base": args.base, "affected_files": sorted(changed),
              "changed_symbols": symbols, "nearby_callers": callers,
              "diagnostics": diagnostics})


def watch_command(args):
    root = Path(args.root).resolve()
    try:
        while True:
            workspace = Workspace(root).build()
            if workspace.changed:
                json_out({"event": "index_updated", "changed_files": sorted(workspace.changed),
                          "cache": {"hits": workspace.cache_hits, "misses": workspace.cache_misses}})
                sys.stdout.flush()
            if args.once:
                return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return


def lsp_send(message):
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


def uri_path(uri):
    parsed = urlparse(uri)
    return Path(unquote(parsed.path))


def lsp_server(default_root):
    workspace = Workspace(default_root)
    documents = {}
    while True:
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            chunk = sys.stdin.buffer.read(1)
            if not chunk:
                return
            header += chunk
        match = re.search(rb"Content-Length:\s*(\d+)", header, re.I)
        if not match:
            return
        body = sys.stdin.buffer.read(int(match.group(1)))
        try:
            message = json.loads(body)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        params = message.get("params", {})
        request_id = message.get("id")
        if method == "initialize":
            params = message.get("params", {})
            root_uri = params.get("rootUri")
            if not root_uri and params.get("workspaceFolders"):
                root_uri = params["workspaceFolders"][0].get("uri")
            candidate = uri_path(root_uri).resolve() if root_uri else default_root
            if not candidate.is_dir():
                candidate = default_root
            workspace = Workspace(candidate).build()
            lsp_send({"jsonrpc": "2.0", "id": request_id, "result": {
                "capabilities": {"textDocumentSync": 1, "definitionProvider": True,
                    "referencesProvider": True, "renameProvider": {"prepareProvider": False},
                    "documentSymbolProvider": True, "workspaceSymbolProvider": True},
                "serverInfo": {"name": "AthenaCtx", "version": "0.2.0"}}})
        elif method == "shutdown":
            lsp_send({"jsonrpc": "2.0", "id": request_id, "result": None})
        elif method == "exit":
            return
        elif method == "textDocument/didOpen":
            doc = params.get("textDocument", {})
            uri = doc.get("uri", "")
            documents[uri] = doc.get("text", "")
            path = uri_path(uri).resolve()
            if path.is_relative_to(workspace.root):
                workspace.update_document(path.relative_to(workspace.root).as_posix(), documents[uri])
        elif method == "textDocument/didChange":
            doc = params.get("textDocument", {})
            changes = params.get("contentChanges", [])
            if changes:
                uri = doc.get("uri", "")
                documents[uri] = changes[-1].get("text", documents.get(uri, ""))
                path = uri_path(uri).resolve()
                if path.is_relative_to(workspace.root):
                    workspace.update_document(path.relative_to(workspace.root).as_posix(), documents[uri])
        elif method in {"textDocument/definition", "textDocument/references", "textDocument/rename"}:
            uri = params.get("textDocument", {}).get("uri", "")
            path = uri_path(uri)
            try:
                rel = path.resolve().relative_to(workspace.root).as_posix()
            except ValueError:
                rel = ""
            text = documents.get(uri, workspace.sources.get(rel, ""))
            pos = params.get("position", {})
            lines = text.splitlines()
            line_no = int(pos.get("line", 0))
            line = lines[line_no] if line_no < len(lines) else ""
            col = int(pos.get("character", 0))
            match = next((item for item in re.finditer(r"[A-Za-z_$][\w$]*", line)
                          if item.start() <= col <= item.end()), None)
            name = match.group(0) if match else ""
            refs = workspace.query_references(name)
            if method == "textDocument/definition":
                result = [lsp_location(ref) for ref in refs if ref["role"] == "definition"]
            elif method == "textDocument/references":
                result = [lsp_location(ref) for ref in refs
                          if params.get("context", {}).get("includeDeclaration", True) or ref["role"] != "definition"]
            else:
                edits = {}
                for ref in refs:
                    target_uri = Path(ref["file"]).resolve().as_uri()
                    edits.setdefault(target_uri, []).append({"range": lsp_location(ref)["range"],
                                                              "newText": params.get("newName", "")})
                result = {"changes": edits}
            lsp_send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "textDocument/documentSymbol":
            uri = params.get("textDocument", {}).get("uri", "")
            path = uri_path(uri)
            try:
                rel = path.resolve().relative_to(workspace.root).as_posix()
            except ValueError:
                rel = ""
            scan = workspace.scans.get(rel, {})
            result = [{"name": s.get("name", ""), "kind": 12, "range": {
                "start": {"line": max(0, s.get("line", 1) - 1), "character": 0},
                "end": {"line": max(0, s.get("line", 1) - 1), "character": 0}},
                "selectionRange": {"start": {"line": max(0, s.get("line", 1) - 1), "character": 0},
                                   "end": {"line": max(0, s.get("line", 1) - 1), "character": 0}}}
                for s in scan.get("symbols", [])]
            lsp_send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "workspace/symbol":
            query = params.get("query", "").lower()
            result = []
            for rel, scan in workspace.scans.items():
                for symbol in scan.get("symbols", []):
                    if query in symbol.get("name", "").lower():
                        line = max(0, symbol.get("line", 1) - 1)
                        result.append({"name": symbol.get("name", ""), "kind": 12,
                                       "location": {"uri": (workspace.root / rel).as_uri(), "range": {
                                           "start": {"line": line, "character": 0},
                                           "end": {"line": line, "character": 0}}}})
            lsp_send({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif request_id is not None:
            lsp_send({"jsonrpc": "2.0", "id": request_id, "result": None})


def lsp_location(ref):
    line = max(0, int(ref.get("line", 1)) - 1)
    col = max(0, int(ref.get("column", 1)) - 1)
    name = ref.get("name", "")
    return {"uri": Path(ref["file"]).resolve().as_uri(), "range": {
        "start": {"line": line, "character": col},
        "end": {"line": line, "character": col + len(name)}}}


def parser():
    root_default = str(Path.cwd())
    p = argparse.ArgumentParser(prog="athenactx")
    sub = p.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index", help="index a workspace and cache per-file scans")
    index.add_argument("root", nargs="?", default=root_default)
    index.add_argument("--force", action="store_true")
    imports = sub.add_parser("imports", help="scan an entry file and its local import closure")
    imports.add_argument("entry")
    imports.add_argument("--root")
    imports.add_argument("--max-depth", type=int, default=32)
    imports.add_argument("--max-files", type=int, default=256)
    refs = sub.add_parser("references", help="find syntactic workspace references")
    refs.add_argument("symbol")
    refs.add_argument("--root", default=root_default)
    query = sub.add_parser("query", help="filter indexed symbols, calls, and diagnostics")
    query.add_argument("--root", default=root_default)
    query.add_argument("--kind")
    query.add_argument("--name")
    query.add_argument("--caller")
    query.add_argument("--callee")
    query.add_argument("--file")
    query.add_argument("--diagnostics", action="store_true")
    rename = sub.add_parser("rename", help="preview or apply syntactic identifier edits")
    rename.add_argument("old_name")
    rename.add_argument("new_name")
    rename.add_argument("--root", default=root_default)
    rename.add_argument("--apply", action="store_true")
    caps = sub.add_parser("capabilities", help="report language extraction support")
    caps.add_argument("language")
    diff = sub.add_parser("diff", help="summarize changed files and their symbols/callers")
    diff.add_argument("--root", default=root_default)
    diff.add_argument("--base", default="HEAD")
    watch = sub.add_parser("watch", help="incrementally update workspace cache")
    watch.add_argument("--root", default=root_default)
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--once", action="store_true")
    lsp = sub.add_parser("lsp", help="serve the workspace over stdio LSP")
    lsp.add_argument("--root", default=root_default)
    return p


def main():
    args = parser().parse_args()
    try:
        if args.command == "index":
            index_command(args)
        elif args.command == "imports":
            if args.max_depth < 0 or args.max_files < 1:
                raise ValueError("--max-depth must be non-negative and --max-files must be positive")
            scan_imports_command(args)
        elif args.command == "references":
            references_command(args)
        elif args.command == "query":
            query_command(args)
        elif args.command == "rename":
            rename_command(args)
        elif args.command == "capabilities":
            capabilities_command(args)
        elif args.command == "diff":
            diff_command(args)
        elif args.command == "watch":
            watch_command(args)
        elif args.command == "lsp":
            lsp_server(Path(args.root).resolve())
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
