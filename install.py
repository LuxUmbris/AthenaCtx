#!/usr/bin/env python3
"""Install the compiled AthenaCtx executable and optional agent instructions."""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROMPT_BEGIN = "<!-- BEGIN ATHENACTX MANAGED INSTRUCTIONS -->"
PROMPT_END = "<!-- END ATHENACTX MANAGED INSTRUCTIONS -->"
AGENTS = (
    "hermes",
    "openclaw",
    "github-copilot",
    "codex",
    "claude-code",
    "opencode",
)


def detect_platform(system: str | None = None) -> str:
    name = system or platform.system()
    if name == "Linux":
        return "linux"
    if name == "Darwin":
        return "macos"
    if name == "Windows":
        return "windows"
    raise ValueError(f"unsupported operating system: {name}")


def prompt_target(
    agent: str,
    repo: Path | None,
    home: Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    home = home or Path.home()
    env = os.environ if env is None else env
    project_files = {
        "hermes": ".hermes.md",
        "openclaw": "AGENTS.md",
        "github-copilot": ".github/copilot-instructions.md",
        "codex": "AGENTS.md",
        "claude-code": "CLAUDE.md",
        "opencode": "AGENTS.md",
    }
    if agent not in project_files:
        raise ValueError(f"unknown agent integration: {agent}")
    if repo is not None:
        return repo / project_files[agent]
    if agent == "hermes":
        hermes_home = env.get("HERMES_HOME")
        if hermes_home:
            return Path(hermes_home).expanduser() / "SOUL.md"
        hermes = shutil.which("hermes")
        if hermes:
            result = subprocess.run(
                [hermes, "config", "path"], capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip()).expanduser().parent / "SOUL.md"
        return home / ".hermes" / "SOUL.md"
    if agent == "openclaw":
        workspace = env.get("OPENCLAW_WORKSPACE_DIR")
        if workspace:
            return Path(workspace).expanduser() / "AGENTS.md"
        profile = env.get("OPENCLAW_PROFILE", "default")
        state = env.get("OPENCLAW_STATE_DIR")
        if state:
            return Path(state).expanduser() / "workspace" / "AGENTS.md"
        state_home = home / (".openclaw" if profile == "default" else f".openclaw-{profile}")
        return state_home / "workspace" / "AGENTS.md"
    if agent == "github-copilot":
        raise ValueError(
            "GitHub Copilot has no local global-instructions file; use --repo PATH "
            "to merge .github/copilot-instructions.md"
        )
    if agent == "codex":
        return home / ".codex" / "AGENTS.md"
    if agent == "claude-code":
        return home / ".claude" / "CLAUDE.md"
    config_home = Path(env.get("XDG_CONFIG_HOME", str(home / ".config"))).expanduser()
    return config_home / "opencode" / "AGENTS.md"


def merge_prompt(path: Path, prompt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    start_count = old.count(PROMPT_BEGIN)
    end_count = old.count(PROMPT_END)
    if start_count != end_count or start_count > 1:
        raise ValueError(f"unbalanced or duplicate AthenaCtx markers in {path}")
    block = f"{PROMPT_BEGIN}\n{prompt.rstrip()}\n{PROMPT_END}"
    if start_count:
        start = old.index(PROMPT_BEGIN)
        end = old.index(PROMPT_END, start) + len(PROMPT_END)
        merged = old[:start] + block + old[end:]
    else:
        separator = "" if not old or old.endswith("\n") else "\n"
        merged = old + separator + ("\n" if old else "") + block + "\n"
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent,
        prefix=f".{path.name}.", delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(merged)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def binary_source(root: Path, platform_name: str) -> Path:
    candidates = [root / "build" / "athenactx.exe", root / "build" / "athenactx"]
    if platform_name != "windows":
        candidates.reverse()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    compiler = shutil.which("shaftc")
    if compiler:
        result = subprocess.run([compiler, "--build", str(root / "Shaft.toml")], cwd=root)
        if result.returncode == 0:
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
    raise FileNotFoundError(
        f"compiled AthenaCtx binary not found; build it with `shaftc --build Shaft.toml` in {root}"
    )


def install_binary(source: Path, bin_dir: Path, platform_name: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    name = "athenactx.exe" if platform_name == "windows" else "athenactx"
    target = bin_dir / name
    shutil.copy2(source, target)
    if platform_name != "windows":
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def pip_command() -> list[str]:
    candidates = [
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
        str(Path(sys.base_prefix) / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")),
        "/usr/bin/python3",
        "/usr/local/bin/python3",
    ]
    checked = set()
    for candidate in candidates:
        if not candidate or candidate in checked:
            continue
        checked.add(candidate)
        probe = subprocess.run(
            [candidate, "-m", "pip", "--version"], capture_output=True, text=True
        )
        if probe.returncode == 0:
            return [candidate]
    launcher = shutil.which("py") if os.name == "nt" else None
    if launcher:
        probe = subprocess.run(
            [launcher, "-3", "-m", "pip", "--version"], capture_output=True, text=True
        )
        if probe.returncode == 0:
            return [launcher, "-3"]
    raise RuntimeError("no Python interpreter with pip is available to install parser dependencies")


def install_runtime_assets(root: Path, prefix: Path) -> Path:
    assets = prefix / "lib" / "athenactx"
    assets.mkdir(parents=True, exist_ok=True)
    tools = root / "tools"
    if tools.is_dir():
        shutil.copytree(tools, assets / "tools", dirs_exist_ok=True)
    requirements = root / "requirements.txt"
    if requirements.is_file():
        subprocess.run(
            [*pip_command(), "-m", "pip", "install", "--disable-pip-version-check",
             "--upgrade", "--target", str(assets / "vendor"), "-r", str(requirements)],
            check=True,
        )
    return assets


def add_unix_path(bin_dir: Path, home: Path, platform_name: str, shell: str) -> Path | None:
    if str(bin_dir) in os.environ.get("PATH", "").split(os.pathsep):
        return None
    if Path(shell).name == "fish":
        profile = home / ".config" / "fish" / "conf.d" / "athenactx.fish"
        line = f"fish_add_path {shlex.quote(str(bin_dir))}"
    else:
        profile_name = ".zprofile" if platform_name == "macos" and Path(shell).name == "zsh" else ".profile"
        profile = home / profile_name
        line = f"export PATH={shlex.quote(str(bin_dir))}:\"$PATH\""
    begin = "# >>> athenactx PATH >>>"
    end = "# <<< athenactx PATH <<<"
    profile.parent.mkdir(parents=True, exist_ok=True)
    old = profile.read_text(encoding="utf-8") if profile.exists() else ""
    block = f"{begin}\n{line}\n{end}"
    if begin in old and end in old:
        start = old.index(begin)
        finish = old.index(end, start) + len(end)
        new = old[:start] + block + old[finish:]
    else:
        separator = "" if not old or old.endswith("\n") else "\n"
        new = old + separator + ("\n" if old else "") + block + "\n"
    profile.write_text(new, encoding="utf-8")
    return profile


def add_windows_path(bin_dir: Path) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Windows PATH updates are only available on Windows")
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0,
        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
    )
    try:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        entries = [item for item in current.split(os.pathsep) if item]
        if any(os.path.normcase(os.path.normpath(item)) == os.path.normcase(str(bin_dir)) for item in entries):
            return False
        entries.append(str(bin_dir))
        winreg.SetValueEx(key, "Path", 0, value_type, os.pathsep.join(entries))
        return True
    finally:
        winreg.CloseKey(key)


def default_prefix(platform_name: str, home: Path, env: dict[str, str]) -> Path:
    if platform_name == "windows":
        return Path(env.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "AthenaCtx"
    return home / ".local"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, help="installation prefix")
    parser.add_argument("--binary", type=Path, help="use this already-built executable")
    parser.add_argument("--no-path", action="store_true", help="do not modify the user PATH")
    parser.add_argument("--repo", nargs="?", const=Path("."), type=Path, metavar="PATH",
                        help="merge instructions into a repository instead of global agent files")
    for agent in AGENTS:
        parser.add_argument(f"--{agent}", action="store_true", help=f"merge instructions for {agent}")
    args = parser.parse_args(argv)
    try:
        platform_name = detect_platform()
        home = Path.home()
        prefix = args.prefix.expanduser() if args.prefix else default_prefix(platform_name, home, os.environ)
        bin_dir = prefix / "bin"
        repo = args.repo.resolve() if args.repo is not None else None
        selected = [agent for agent in AGENTS if getattr(args, agent.replace("-", "_"))]
        targets = [(agent, prompt_target(agent, repo, home)) for agent in selected]
        source = args.binary.expanduser() if args.binary else binary_source(ROOT, platform_name)
        installed = install_binary(source, bin_dir, platform_name)
        install_runtime_assets(ROOT, prefix)
        if args.no_path:
            changed_path = False
        elif platform_name == "windows":
            changed_path = add_windows_path(bin_dir)
        else:
            profile = add_unix_path(bin_dir, home, platform_name, os.environ.get("SHELL", ""))
            changed_path = profile is not None
        prompt = (ROOT / "SYSTEM_PROMPT.md").read_text(encoding="utf-8")
        for agent, target in targets:
            merge_prompt(target, prompt)
            print(f"merged {agent}: {target}")
        print(f"installed: {installed}")
        if args.no_path:
            print("PATH update skipped")
        elif changed_path:
            print("PATH updated; open a new shell to use athenactx")
        else:
            print("install directory is already on PATH")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
