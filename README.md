# AthenaCTX

AthenaCTX is a multi-language source indexer with a [Shaft](https://github.com/LuxUmbris/Shaft/tree/beta-0.3-bootstrap) CLI and language-specific analyzers.

## Build and setup

```sh
./setup.sh
shaftc --build Shaft.toml
```

`setup.sh` installs the pinned Tree-sitter language pack into ignored `vendor/`. The `athenactx` launcher configures the dependency, resolves `shaftc` from `PATH`, and runs `build/athenactx`. First scans download the requested parser grammar, so they need network access. `athenactx languages` lists the 372 grammars in the pinned pack manifest.

```sh
./athenactx scan path/to/file.py
./athenactx scan path/to/file.unknown rust
./athenactx scan path/to/file.shaft
./athenactx implementation path/to/file.py 'def render(self) -> str:'
./athenactx prompt
```

`scan` auto-detects languages from file extensions; the optional third argument overrides detection. Shaft files use the Shaft-specific analyzer plus `shaftc --check-only` diagnostics when invoked through the launcher. Other files use Tree-sitter. The pack covers 372 grammars, not every third-party grammar ever published outside that pack.

## Workspace features

```sh
./athenactx index .                         # Project index; cached in .athenactx/index.json
./athenactx scan .                          # Alias for workspace indexing
./athenactx references render --root .       # Syntactic references across indexed files
./athenactx query --root . --kind function --name render
./athenactx query --root . --caller main --callee render --diagnostics
./athenactx rename render render_view --root .  # Preview only
./athenactx rename render render_view --root . --apply
./athenactx capabilities python
./athenactx watch --root .                   # Poll and update changed-file cache entries
./athenactx diff --root . --base HEAD
./athenactx lsp --root .                     # LSP over Content-Length framed stdio
```

The project index skips common generated/dependency directories, caches each file's scan using its content fingerprint, and only re-scans changed files. It emits symbols, syntactic references, parser diagnostics, import links, and cache statistics as stable JSON. `references` and `rename` use exact identifier tokens where a syntax inventory is available; rename is a preview unless `--apply` is given. These are name-based syntactic matches, not compiler-resolved references, so review rename previews before applying them.

`capabilities LANGUAGE` reports parser availability, whether the pinned pack has declaration tags, and whether extraction is tagged or heuristic. Parsing support does not imply semantic correctness. The LSP currently supports initialize/shutdown, full-document open/change synchronization, definition, references, rename edits, document symbols, and workspace symbols. It uses the same syntactic index and makes no type-resolution guarantee. Start it from an editor using `./athenactx lsp --root /path/to/workspace` with stdio transport.

`diff` combines Git's changed-file list with files changed since the last cached index; its summary includes symbols in affected files, matching call sites elsewhere, and diagnostics. `watch` is a polling mode; set `--interval SECONDS` to change its default one-second interval, or `--once` to perform one update check and exit.

## Index contents

The JSON result has `file`, `language`, `symbols`, `calls`, `effects`, `diagnostics`, and `syntax` fields.

- `symbols` includes recognized functions, classes, structs, traits/interfaces, impls, enums, unions, macros, constants/types, fields, enum members, and union variants. Pack tag queries are used where available; a node-name fallback handles common forms. Member symbols include their container when exposed by the grammar.
- `calls` records syntactic callee names and the enclosing function when identifiable.
- `effects` contains name-based hints such as `io_or_external`; it is not a sound or transitive effect system.
- `diagnostics` reports Tree-sitter `ERROR`/missing nodes and compiler diagnostics for Shaft files. Structural brace errors are also reported.
- `syntax` lists named Tree-sitter nodes with node type, field name, nesting depth, byte range, line, and column. It does not include source bodies or literal contents.

All 372 grammars can parse and provide syntax nodes and parser errors. Declaration tagging varies: only grammars with shipped tag queries have language-specific definitions; other grammars use common node-name heuristics. Names are not type-resolved.

`implementation` accepts an exact signature from the scan and returns only that function body. It rejects unmatched signatures. For an extension Tree-sitter detects poorly, pass the language explicitly to `scan`.

## Source layout

- `src/main.shaft`: CLI dispatch and language selection.
- `src/treesitter.shaft`: node-record indexing, calls, effects, diagnostics, and implementation lookup.
- `src/shaft.shaft`: Shaft declarations, fields, enum members, union variants, brace checks, and implementation lookup.
- `src/diagnostics.shaft`: shared JSON diagnostic formatting.
- `tools/treesitter_bridge.py`: thin binding adapter that parses through Tree-sitter and serializes node/tag records for the Shaft analyzer.

The bridge supplies parsing; extraction, output shaping, and the Shaft-specific path are implemented in Shaft modules.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests cover Shaft structs/enums/unions/macros and compiler errors, Python/Rust/JavaScript/Bash/C scans, exact implementation requests, parser diagnostics, workspace links/references/cache/query/rename, diff/watch behavior, LSP framing/live-document updates, and the grammar registry.

The longer agent prompt is in `SYSTEM_PROMPT.md`; `./athenactx prompt` prints its concise runtime form.
