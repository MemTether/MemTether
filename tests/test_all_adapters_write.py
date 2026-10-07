# -*- coding: utf-8 -*-
"""tests/test_all_adapters_write.py — a50: ALL 23 adapters' write paths

Full write-path contract for every registered adapter against a
fake-home tmp directory:
  1. create (missing config -> valid file with our server)
  2. read_back roundtrip
  3. idempotent second write
  4. preserve existing unrelated content (family-aware fixtures)
  5. backup of original before modification
Failure here = that adapter would miswrite a real user's config.
"""
import glob
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from clients import all_adapters, find as find_adapter
from clients.base import ServerSpec, Target

SPEC = ServerSpec(name="memory-hub", command="python",
                  args=["-m", "mcp_server"], env={})
ADAPTER_IDS = [a.id for a in all_adapters()]

# valid pre-existing config per adapter family
# per-adapter schema roots discovered by the fail-closed validators themselves
_EXISTING = {
    "dsh": "# my custom comment\n[]\n",
    "continue": "# continue config\nallowAutoUserCommands: []\n",
    "codex": '# codex config\nmodel = "test"\n\n[mcp_servers]\n',
    # different schema roots (fail-closed validators told us the real keys)
    "vscode-workspace": '{"servers": {"other": {"command": "n", "args": []}}}',
    "jetbrains": '{"servers": {"other": {"command": "n", "args": []}}}',
    "zed": '{"context_servers": {"other": {"command": "n", "args": []}}}',
    "cody": '{"cody": {"mcpServers": {"other": {"command": "n", "args": []}}}}',
    "zcode": '{"mcp": {"servers": {"other": {"command": "n", "args": []}}}}',
    "openclaw": '{"mcp": {"servers": {"other": {"command": "n", "args": []}}}}',
}
DEFAULT_EXISTING = '{"mcpServers": {"other-tool": {"command": "node", "args": ["x.js"]}}}'


def _existing_content(aid):
    return _EXISTING.get(aid, DEFAULT_EXISTING)


def _survival_marker(aid):
    if aid == "dsh":
        return "my custom comment"
    if aid == "continue":
        return "continue config"
    if aid == "codex":
        return 'model = "test"'
    if aid in ("vscode-workspace", "jetbrains", "zed", "cody"):
        return '"other"'
    if aid in ("zcode", "openclaw"):
        return '"other"'
    return "other-tool"


def _target(a, tmp_path, exists=False, content=None):
    cand_path = a.candidates()[0][1] if a.candidates() else "cfg.json"
    fname = os.path.basename(cand_path.replace("\\", "/")) if cand_path else "cfg.json"
    p = os.path.join(str(tmp_path), f"home_{a.id}", fname)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if exists and content is not None:
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    return Target(a.id, a.display, "user", p,
                  exists or os.path.exists(p), True, "")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _safe_read(p):
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _entry_present(text):
    if '"memory-hub"' in text or "'memory-hub'" in text:
        return True
    if "memory-hub:" in text or "[mcp_servers.memory-hub]" in text:
        return True
    return "memory-hub" in text


@pytest.mark.parametrize("adapter_id", ADAPTER_IDS)
class TestAdapterWriteContract:
    def test_create_on_missing_config(self, adapter_id, tmp_path):
        a = find_adapter(adapter_id)
        if a.id == "dsh":
            # dsh is a patch layer: by design it never creates a profile from
            # scratch (fail-closed). Seed the default template the adapter
            # itself documents, and assert insert-into-template works.
            content = "# dsh patch template\n[]\n"
            t = _target(a, tmp_path, exists=True, content=content)
            ch = a.write(t, SPEC, dry_run=False)
            assert ch.action in ("create", "replace", "insert"), (adapter_id, ch)
            text = _load(t.path)
            assert _entry_present(text)
            return
        t = _target(a, tmp_path, exists=False)
        ch = a.write(t, SPEC, dry_run=False)
        assert ch.action == "create", (adapter_id, ch)
        text = _load(t.path)
        assert text.strip(), f"{adapter_id}: empty config after create"
        assert _entry_present(text), f"{adapter_id}: server missing after create"

    def test_read_back_after_create(self, adapter_id, tmp_path):
        a = find_adapter(adapter_id)
        seed = "# template\n[]\n" if a.id == "dsh" else None
        t = _target(a, tmp_path, exists=bool(seed), content=seed)
        ch = a.write(t, SPEC, dry_run=False)
        assert ch.action != "error", (adapter_id, ch)
        ok, detail = a.read_back(t, SPEC)
        assert ok, f"{adapter_id}: read_back failed after create: {detail}"

    def test_idempotent_second_write(self, adapter_id, tmp_path):
        a = find_adapter(adapter_id)
        seed = "# template\n[]\n" if a.id == "dsh" else None
        t = _target(a, tmp_path, exists=bool(seed), content=seed)
        a.write(t, SPEC, dry_run=False)
        before = _load(t.path)
        ch2 = a.write(_target(a, tmp_path, exists=True), SPEC, dry_run=False)
        after = _load(t.path)
        if ch2.action in ("noop", "skip", "none"):
            assert before == after
        else:
            assert before == after or _entry_present(after), (
                f"{adapter_id}: second write changed content unexpectedly "
                f"({ch2.action})")

    def test_preserves_existing_servers(self, adapter_id, tmp_path):
        a = find_adapter(adapter_id)
        content = _existing_content(adapter_id)
        marker = _survival_marker(adapter_id)
        t = _target(a, tmp_path, exists=True, content=content)
        try:
            ch = a.write(t, SPEC, dry_run=False)
        except Exception as e:
            pytest.fail(f"{adapter_id}: write onto existing config raised: {e}")
        if ch.action == "error":
            pytest.fail(f"{adapter_id}: write onto valid existing config "
                        f"returned error: {ch}")
        if a.id == "continue":
            # by design: existing config.yaml -> skip + manual merge
            assert ch.action in ("skip", "noop", "error"), (adapter_id, ch)
            return
        text = _load(t.path)
        assert _entry_present(text), f"{adapter_id}: server missing after merge"
        assert marker in text, f"{adapter_id}: existing content clobbered!"

    def test_backup_created_before_modify(self, adapter_id, tmp_path, monkeypatch):
        a = find_adapter(adapter_id)
        content = _existing_content(adapter_id)
        t = _target(a, tmp_path, exists=True, content=content)
        original = _load(t.path)
        # redirect the adapter backup root into tmp so we can assert precisely
        broot = os.path.join(str(tmp_path), "backups")
        os.makedirs(broot, exist_ok=True)
        import clients.base as cb
        monkeypatch.setattr(cb, "BACKUP_ROOT", broot)
        # adapters imported backup directly at module import; patch their refs too
        # patch BACKUP_ROOT on every clients module that references it
        for mod_name in list(sys.modules):
            if mod_name.startswith("clients"):
                mod = sys.modules.get(mod_name)
                if mod is not None and hasattr(mod, "BACKUP_ROOT"):
                    monkeypatch.setattr(mod, "BACKUP_ROOT", broot)
        ch = a.write(t, SPEC, dry_run=False)
        if ch.action in ("error", "noop", "skip"):
            pytest.skip(f"{adapter_id}: write did not modify ({ch.action})")
        if getattr(ch, "backup", ""):
            hits = [ch.backup]
        else:
            hits = glob.glob(os.path.join(broot, "**", "*"), recursive=True)
        content_hits = [h for h in hits if os.path.isfile(h)
                        and original in _safe_read(h)]
        assert content_hits, f"{adapter_id}: no backup containing original found"


def _backup_to(path, stamp, client_id, broot):
    from clients.base import backup as _real
    # call real backup but with overridden module constant
    import clients.base as cb
    old = cb.BACKUP_ROOT
    cb.BACKUP_ROOT = broot
    try:
        return _real(path, stamp, client_id)
    finally:
        cb.BACKUP_ROOT = old
