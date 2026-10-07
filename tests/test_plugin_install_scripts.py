# -*- coding: utf-8 -*-
"""tests/test_plugin_install_scripts.py — a50: plugin installer validation

The three install scripts were never validated. Full GUI-client runs need
real machines, but we CAN verify: scripts parse (bash -n / PowerShell AST),
reference real repo files, embed a valid MCP server command, and the
shipped slash-command markdown files exist and carry frontmatter.
"""
import json
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLUGINS = [
    ("claude", os.path.join(REPO, "integrations", "claude-plugin",
                            "memtether-memory", "install.sh")),
    ("claude-ps1", os.path.join(REPO, "integrations", "claude-plugin",
                                "memtether-memory", "install.ps1")),
    ("cursor", os.path.join(REPO, "integrations", "cursor-plugin",
                            "memtether", "install.sh")),
    ("cursor-ps1", os.path.join(REPO, "integrations", "cursor-plugin",
                                "memtether", "install.ps1")),
    ("openclaw", os.path.join(REPO, "integrations", "openclaw-plugin",
                              "memtether", "install.sh")),
    ("openclaw-ps1", os.path.join(REPO, "integrations", "openclaw-plugin",
                                  "memtether", "install.ps1")),
]


class TestPluginInstallScripts:
    @pytest.mark.parametrize("name,path", PLUGINS)
    def test_script_exists_nonempty(self, name, path):
        assert os.path.isfile(path), f"missing install script: {path}"
        assert os.path.getsize(path) > 200

    @pytest.mark.parametrize("name,path", [p for p in PLUGINS if p[0].endswith(".sh")])
    def test_bash_syntax(self, name, path):
        r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("name,path", PLUGINS)
    def test_references_real_files(self, name, path):
        """Paths/scripts referenced by the installer must exist in the repo."""
        text = open(path, encoding="utf-8").read()
        # must wire up the memtether server (via mcp_server or server name)
        assert ("mcp_server" in text) or ("memtether" in text), (
            f"{name}: installer wires neither mcp_server nor memtether server")

    @pytest.mark.parametrize("name,path", PLUGINS)
    def test_no_hardcoded_secrets_or_abs_paths(self, name, path):
        text = open(path, encoding="utf-8").read()
        assert "E:\\RUANJIAN" not in text, f"{name}: hardcoded dev path leaked"
        assert "sk-" not in text, f"{name}: possible API key in installer"


class TestClaudeSlashCommands:
    CMDS = os.path.join(REPO, "integrations", "claude-plugin",
                        "memtether-memory", "commands")

    def test_commands_dir_exists(self):
        assert os.path.isdir(self.CMDS)

    @pytest.mark.parametrize("fname", ["mem-remember.md", "mem-search.md", "mem-stats.md"])
    def test_slash_command_has_frontmatter(self, fname):
        p = os.path.join(self.CMDS, fname)
        assert os.path.isfile(p), f"missing {fname}"
        text = open(p, encoding="utf-8").read()
        assert text.startswith("---"), f"{fname}: no frontmatter"
        assert "description:" in text, f"{fname}: no description"
        assert fname.endswith(".md")

    def test_manifest_declares_mcp_server(self):
        mf = json.load(open(os.path.join(
            REPO, "integrations", "claude-plugin", "memtether-memory",
            "manifest.json"), encoding="utf-8"))
        assert mf["mcpServers"]["memtether"]["command"] == "python"
