"""Local import discovery adapters for common source-language families."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class ImportResolver:
    extract: Callable[[str, Path, Path], Iterable[str]]
    resolve: Callable[[str, Path, Path], Iterable[Path]]


_RESOLVERS: dict[str, ImportResolver] = {}


def register_resolver(languages: Iterable[str], resolver: ImportResolver) -> None:
    for language in languages:
        _RESOLVERS[language.lower()] = resolver


def resolver_for(language: str) -> ImportResolver | None:
    return _RESOLVERS.get(language.lower())


def _within_root(path: Path, root: Path) -> Path | None:
    candidate = path.resolve()
    return candidate if candidate.is_relative_to(root.resolve()) and candidate.is_file() else None


def _try_paths(root: Path, bases: Iterable[Path], suffixes: tuple[str, ...]) -> list[Path]:
    found = []
    seen = set()
    for base in bases:
        candidates = [base]
        if not base.suffix:
            candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
            candidates.extend(base / f"index{suffix}" for suffix in suffixes)
            candidates.extend((base / "__init__.py", base / "mod.rs"))
        for candidate in candidates:
            safe = _within_root(candidate, root)
            if safe and safe not in seen:
                seen.add(safe)
                found.append(safe)
    return found


def _python_imports(source: str, path: Path, root: Path) -> Iterable[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    package_parts = list(path.parent.relative_to(root).parts)
    specs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            specs.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: max(0, len(package_parts) - node.level + 1)]
                if node.module:
                    base.extend(node.module.split("."))
                module = ".".join(base)
            else:
                module = node.module or ""
            if module:
                specs.append(module)
                specs.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return specs


def _python_resolve(spec: str, path: Path, root: Path) -> Iterable[Path]:
    parts = spec.split(".")
    base = root.joinpath(*parts)
    matches = _try_paths(root, [base], (".py", ".pyi"))
    package = root
    for part in parts[:-1]:
        package = package / part
        initializer = _within_root(package / "__init__.py", root)
        if initializer and initializer not in matches:
            matches.insert(0, initializer)
    return matches


def _js_imports(source: str, path: Path, root: Path) -> Iterable[str]:
    patterns = (
        r"\b(?:import|export)\s+(?:[^;]*?\s+from\s+)?['\"]([^'\"]+)['\"]",
        r"\b(?:require|import)\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
    )
    return [match.group(1) for pattern in patterns for match in re.finditer(pattern, source)]


def _js_resolve(spec: str, path: Path, root: Path) -> Iterable[Path]:
    if not spec.startswith((".", "/")):
        return ()
    base = (path.parent / spec) if not spec.startswith("/") else (root / spec.lstrip("/"))
    return _try_paths(root, [base], (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".json"))


def _rust_imports(source: str, path: Path, root: Path) -> Iterable[str]:
    specs = [f"mod:{match.group(1)}" for match in re.finditer(r"\bmod\s+([A-Za-z_]\w*)\s*;", source)]
    specs.extend(match.group(1) for match in re.finditer(
        r"\buse\s+(?:crate|self|super)::([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)", source
    ))
    return specs


def _rust_resolve(spec: str, path: Path, root: Path) -> Iterable[Path]:
    if spec.startswith("mod:"):
        name = spec[4:]
        return _try_paths(root, [path.parent / name], (".rs",))
    parts = spec.split("::")
    bases = [root.joinpath(*parts), path.parent.joinpath(*parts)]
    for end in range(len(parts) - 1, 0, -1):
        bases.append(root.joinpath(*parts[:end]))
    return _try_paths(root, bases, (".rs",))


def _quoted_imports(pattern: str) -> Callable[[str, Path, Path], Iterable[str]]:
    compiled = re.compile(pattern, re.MULTILINE)

    def extract(source: str, path: Path, root: Path) -> Iterable[str]:
        return [match.group(1) for match in compiled.finditer(source)]

    return extract


def _relative_resolve(suffixes: tuple[str, ...]) -> Callable[[str, Path, Path], Iterable[Path]]:
    def resolve(spec: str, path: Path, root: Path) -> Iterable[Path]:
        base = Path(spec)
        if base.is_absolute():
            return _try_paths(root, [root / str(base).lstrip("/")], suffixes)
        return _try_paths(root, [path.parent / base, root / base], suffixes)

    return resolve


def _java_imports(source: str, path: Path, root: Path) -> Iterable[str]:
    return [match.group(1) for match in re.finditer(
        r"^\s*import\s+(?:static\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
        source,
        re.MULTILINE,
    )]


def _java_resolve(spec: str, path: Path, root: Path) -> Iterable[Path]:
    parts = spec.split(".")
    bases = [root.joinpath(*parts), root.joinpath(*parts[:-1], parts[-1])]
    return _try_paths(root, bases, (".java", ".kt", ".scala"))


register_resolver(("python",), ImportResolver(_python_imports, _python_resolve))
register_resolver(("javascript", "jsx", "typescript", "tsx"), ImportResolver(_js_imports, _js_resolve))
register_resolver(("rust",), ImportResolver(_rust_imports, _rust_resolve))
register_resolver(("c", "cpp", "c++", "objective_c", "objective-c"), ImportResolver(
    _quoted_imports(r"^\s*#\s*include\s*\"([^\"]+)\""),
    _relative_resolve((".h", ".hh", ".hpp", ".hxx", ".inc")),
))
register_resolver(("shaft",), ImportResolver(
    _quoted_imports(r"\bimport\s+\"([^\"]+)\""),
    _relative_resolve((".shaft",)),
))
register_resolver(("java", "kotlin", "scala"), ImportResolver(_java_imports, _java_resolve))
