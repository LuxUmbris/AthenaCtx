import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import install


class InstallerTests(unittest.TestCase):
    def test_merge_preserves_user_text_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "SOUL.md"
            target.write_text("# Existing identity\nKeep this.\n", encoding="utf-8")

            install.merge_prompt(target, "# AthenaCtx\nUse scan first.\n")
            install.merge_prompt(target, "# AthenaCtx updated\nUse the native binary.\n")

            text = target.read_text(encoding="utf-8")
            self.assertIn("# Existing identity\nKeep this.", text)
            self.assertIn("# AthenaCtx updated\nUse the native binary.", text)
            self.assertNotIn("# AthenaCtx\nUse scan first.", text)
            self.assertEqual(text.count(install.PROMPT_BEGIN), 1)
            self.assertEqual(text.count(install.PROMPT_END), 1)

    def test_repo_targets_use_each_agents_supported_context_file(self):
        repo = Path("/project")
        expected = {
            "hermes": repo / ".hermes.md",
            "openclaw": repo / "AGENTS.md",
            "github-copilot": repo / ".github" / "copilot-instructions.md",
            "codex": repo / "AGENTS.md",
            "claude-code": repo / "CLAUDE.md",
            "opencode": repo / "AGENTS.md",
        }
        for agent, path in expected.items():
            with self.subTest(agent=agent):
                self.assertEqual(install.prompt_target(agent, repo, Path("/home/test")), path)

    def test_global_targets_respect_runtime_homes(self):
        home = Path("/home/test")
        env = {
            "HERMES_HOME": str(home / ".hermes" / "profiles" / "work"),
            "OPENCLAW_WORKSPACE_DIR": str(home / "openclaw-workspace"),
            "XDG_CONFIG_HOME": str(home / ".config"),
        }
        self.assertEqual(install.prompt_target("hermes", None, home, env), home / ".hermes" / "profiles" / "work" / "SOUL.md")
        self.assertEqual(install.prompt_target("openclaw", None, home, env), home / "openclaw-workspace" / "AGENTS.md")
        with self.assertRaises(ValueError):
            install.prompt_target("github-copilot", None, home, env)
        self.assertEqual(install.prompt_target("codex", None, home, env), home / ".codex" / "AGENTS.md")
        self.assertEqual(install.prompt_target("claude-code", None, home, env), home / ".claude" / "CLAUDE.md")
        self.assertEqual(install.prompt_target("opencode", None, home, env), home / ".config" / "opencode" / "AGENTS.md")

    def test_platform_detection_is_explicit(self):
        self.assertEqual(install.detect_platform("Linux"), "linux")
        self.assertEqual(install.detect_platform("Darwin"), "macos")
        self.assertEqual(install.detect_platform("Windows"), "windows")
        with self.assertRaises(ValueError):
            install.detect_platform("Plan9")

    def test_installer_selects_a_python_with_pip(self):
        command = install.pip_command()
        result = subprocess.run([*command, "-m", "pip", "--version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unix_path_profile_preserves_text_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            bin_dir = home / ".local" / "bin"
            profile = home / ".profile"
            profile.write_text("# Existing shell config\n", encoding="utf-8")

            first = install.add_unix_path(bin_dir, home, "linux", "/bin/bash")
            second = install.add_unix_path(bin_dir, home, "linux", "/bin/bash")

            self.assertEqual(first, profile)
            self.assertEqual(second, profile)
            text = profile.read_text(encoding="utf-8")
            self.assertIn("# Existing shell config", text)
            self.assertEqual(text.count("# >>> athenactx PATH >>>"), 1)

    def test_installs_binary_and_merges_multiple_repo_prompts(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo = base / "repo"
            repo.mkdir()
            prefix = base / "prefix"
            platform_name = install.detect_platform()
            binary = install.binary_source(Path(install.ROOT), platform_name)

            result = install.main([
                "--binary", str(binary), "--prefix", str(prefix), "--no-path",
                "--repo", str(repo), "--hermes", "--openclaw",
                "--github-copilot", "--codex", "--claude-code", "--opencode",
            ])

            self.assertEqual(result, 0)
            installed = prefix / "bin" / ("athenactx.exe" if platform_name == "windows" else "athenactx")
            self.assertTrue(installed.is_file())
            result = subprocess.run([str(installed), "prompt"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Do not read implementation bodies", result.stdout)
            path_entries = [str(prefix / "bin"), "/usr/bin", "/bin"]
            environment = os.environ.copy()
            environment["PATH"] = os.pathsep.join(path_entries)
            scan = subprocess.run(
                [str(installed), "scan", str(Path(install.ROOT) / "tests" / "fixture.py")],
                cwd=base, env=environment, capture_output=True, text=True,
            )
            self.assertEqual(scan.returncode, 0, scan.stderr)
            self.assertEqual(json.loads(scan.stdout)["language"], "python")
            self.assertIn(install.PROMPT_BEGIN, (repo / ".hermes.md").read_text())
            self.assertIn(install.PROMPT_BEGIN, (repo / ".github" / "copilot-instructions.md").read_text())
            shared = (repo / "AGENTS.md").read_text()
            self.assertEqual(shared.count(install.PROMPT_BEGIN), 1)
            self.assertIn(install.PROMPT_BEGIN, (repo / "CLAUDE.md").read_text())


if __name__ == "__main__":
    unittest.main()
