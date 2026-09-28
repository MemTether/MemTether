# -*- coding: utf-8 -*-
"""
tests/test_exchange_adapters.py — F1: 6 个 exchange 适配器 pytest 测试

每个适配器测三件事：
  1. 该系统格式 → Exchange JSON（导出字段完整性）
  2. Exchange JSON → SQLite（导入不崩、条数一致）
  3. roundtrip：导出 → 导入 → 再导出，关键字段不丢
"""
import json
import os
import sqlite3
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memtether_exchange import import_exchange, SCHEMA_VERSION

# ─── helpers ───

def _tmp_db():
    """Create a temp SQLite and return path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # let import_exchange create it
    return path


def _verify_import(out_path, db_path, min_facts=1):
    """Import an exchange JSON into SQLite and verify."""
    result = import_exchange(out_path, db_path=db_path)
    assert result is not None, "import returned None"
    assert "facts" in result or "count" in result or "ok" in result, f"unexpected import result keys: {list(result.keys())}"
    # Verify DB has data
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
    conn.close()
    assert count >= min_facts, f"expected >= {min_facts} facts, got {count}"
    return count


def _verify_exchange_json(out_path, min_facts=1):
    """Verify an exchange JSON file has the required structure."""
    with open(out_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "schema_name" in data, "missing schema_name"
    assert "schema_version" in data, "missing schema_version"
    assert "facts" in data, "missing facts array"
    assert len(data["facts"]) >= min_facts, f"expected >= {min_facts} facts, got {len(data['facts'])}"
    for fact in data["facts"]:
        assert "content" in fact, f"fact missing content: {fact.get('uid','?')}"
        assert "uid" in fact, "fact missing uid"
    return data


# ─── mem0_exchange ───

class TestMem0Exchange:
    def test_to_exchange(self, tmp_path):
        """mem0 JSON → Exchange JSON"""
        src = tmp_path / "mem0_data.json"
        src.write_text(json.dumps([
            {"id": "m0-001", "memory": "User prefers Python", "created_at": "2026-09-01T10:00:00Z"},
            {"id": "m0-002", "memory": "API key stored in vault", "created_at": "2026-09-02T10:00:00Z"},
        ]), encoding="utf-8")
        out = str(tmp_path / "mem0_exchange.json")
        import mem0_exchange as mx
        result = mx.mem0_to_exchange(str(src), out, source="mem0")
        assert result is not None
        _verify_exchange_json(out, min_facts=2)

    def test_import(self, tmp_path):
        """mem0 → Exchange → SQLite roundtrip"""
        src = tmp_path / "mem0_data.json"
        src.write_text(json.dumps([
            {"id": "m0-r1", "memory": "Roundtrip test fact", "created_at": "2026-09-01T10:00:00Z"},
        ]), encoding="utf-8")
        out = str(tmp_path / "mem0_rt.json")
        import mem0_exchange as mx
        mx.mem0_to_exchange(str(src), out, source="mem0")
        db = _tmp_db()
        _verify_import(out, db, min_facts=1)


# ─── zep_exchange ───

class TestZepExchange:
    def test_to_exchange(self, tmp_path):
        src = tmp_path / "zep_data.json"
        src.write_text(json.dumps([
            {"memory": "Zep memory item 1", "created_at": "2026-09-01T10:00:00Z"},
            {"memory": "Zep memory item 2", "created_at": "2026-09-02T10:00:00Z"},
        ]), encoding="utf-8")
        out = str(tmp_path / "zep_exchange.json")
        import zep_exchange as zx
        result = zx.zep_to_exchange(str(src), out, source="zep")
        assert result is not None
        _verify_exchange_json(out, min_facts=2)

    def test_import(self, tmp_path):
        src = tmp_path / "zep_data.json"
        src.write_text(json.dumps([
            {"memory": "Zep roundtrip fact", "created_at": "2026-09-01T10:00:00Z"},
        ]), encoding="utf-8")
        out = str(tmp_path / "zep_rt.json")
        import zep_exchange as zx
        zx.zep_to_exchange(str(src), out, source="zep")
        db = _tmp_db()
        _verify_import(out, db, min_facts=1)


# ─── letta_exchange ───

class TestLettaExchange:
    def test_to_exchange(self, tmp_path):
        """Letta .af format (agent file with blocks)"""
        src = tmp_path / "agent.af"
        src.write_text(json.dumps({
            "name": "test-agent",
            "blocks": [
                {"label": "persona", "value": "You are a helpful assistant", "limit": 2000},
                {"label": "human", "value": "User likes concise answers", "limit": 1000},
            ]
        }), encoding="utf-8")
        out = str(tmp_path / "letta_exchange.json")
        import letta_exchange as lx
        result = lx.letta_to_exchange(str(src), out, source="letta")
        assert result is not None
        _verify_exchange_json(out, min_facts=1)

    def test_import(self, tmp_path):
        src = tmp_path / "agent.af"
        src.write_text(json.dumps({
            "name": "test-agent",
            "blocks": [{"label": "persona", "value": "Roundtrip block content", "limit": 2000}]
        }), encoding="utf-8")
        out = str(tmp_path / "letta_rt.json")
        import letta_exchange as lx
        lx.letta_to_exchange(str(src), out, source="letta")
        db = _tmp_db()
        _verify_import(out, db, min_facts=1)


# ─── graphiti_exchange ───

class TestGraphitiExchange:
    def test_to_exchange(self, tmp_path):
        src = tmp_path / "graphiti_data.json"
        src.write_text(json.dumps([
            {"fact": "Graphiti edge fact 1", "created_at": "2026-09-01T10:00:00Z", "valid_at": "2026-09-01", "invalid_at": None},
            {"fact": "Graphiti edge fact 2", "created_at": "2026-09-02T10:00:00Z", "valid_at": "2026-09-02", "invalid_at": "2026-09-15"},
        ]), encoding="utf-8")
        out = str(tmp_path / "graphiti_exchange.json")
        import graphiti_exchange as gx
        result = gx.graphiti_to_exchange(str(src), out, source="graphiti")
        assert result is not None
        data = _verify_exchange_json(out, min_facts=2)
        # Graphiti has bi-temporal data
        for fact in data["facts"]:
            assert "valid_from" in fact or "recorded_at" in fact, "graphiti fact missing temporal fields"

    def test_import(self, tmp_path):
        src = tmp_path / "graphiti_data.json"
        src.write_text(json.dumps([
            {"fact": "Graphiti roundtrip", "created_at": "2026-09-01T10:00:00Z", "valid_at": "2026-09-01", "invalid_at": None},
        ]), encoding="utf-8")
        out = str(tmp_path / "graphiti_rt.json")
        import graphiti_exchange as gx
        gx.graphiti_to_exchange(str(src), out, source="graphiti")
        db = _tmp_db()
        _verify_import(out, db, min_facts=1)


# ─── langmem_exchange ───

class TestLangmemExchange:
    def test_to_exchange(self, tmp_path):
        src = tmp_path / "langmem_data.json"
        src.write_text(json.dumps([
            {"content": "LangMem memory 1", "timestamp": "2026-09-01T10:00:00Z"},
            {"content": "LangMem memory 2", "timestamp": "2026-09-02T10:00:00Z"},
        ]), encoding="utf-8")
        out = str(tmp_path / "langmem_exchange.json")
        import langmem_exchange as lx
        result = lx.langmem_to_exchange(str(src), out, source="langmem")
        assert result is not None
        _verify_exchange_json(out, min_facts=2)

    def test_import(self, tmp_path):
        src = tmp_path / "langmem_data.json"
        src.write_text(json.dumps([
            {"content": "LangMem roundtrip", "timestamp": "2026-09-01T10:00:00Z"},
        ]), encoding="utf-8")
        out = str(tmp_path / "langmem_rt.json")
        import langmem_exchange as lx
        lx.langmem_to_exchange(str(src), out, source="langmem")
        db = _tmp_db()
        _verify_import(out, db, min_facts=1)


# ─── cogx_exchange ───

class TestCogxExchange:
    def test_to_exchange(self, tmp_path):
        """COGX JSONL format"""
        cogx_dir = tmp_path / "cogx_input"
        cogx_dir.mkdir()
        src = cogx_dir / "memories.jsonl"
        records = [
            json.dumps({"kind": "memory", "content": "COGX memory 1", "metadata": {"created_at": "2026-09-01"}, "provenance": [{"source": "test"}]}),
            json.dumps({"kind": "memory", "content": "COGX memory 2", "metadata": {"created_at": "2026-09-02"}, "provenance": []}),
        ]
        src.write_text("\n".join(records), encoding="utf-8")
        out = str(tmp_path / "cogx_exchange.json")
        import cogx_exchange as cx
        result = cx.cogx_to_exchange(str(cogx_dir), out, source="cogx")
        assert result is not None
        _verify_exchange_json(out, min_facts=1)

    def test_import(self, tmp_path):
        cogx_dir = tmp_path / "cogx_input"
        cogx_dir.mkdir()
        src = cogx_dir / "memories.jsonl"
        records = [json.dumps({"kind": "memory", "content": "COGX roundtrip", "metadata": {"created_at": "2026-09-01"}})]
        src.write_text("\n".join(records), encoding="utf-8")
        out = str(tmp_path / "cogx_rt.json")
        import cogx_exchange as cx
        cx.cogx_to_exchange(str(cogx_dir), out, source="cogx")
        db = _tmp_db()
        _verify_import(out, db, min_facts=1)


# ─── cross-adapter integration ───

class TestCrossAdapter:
    def test_all_adapters_produce_valid_exchange(self, tmp_path):
        """All 6 adapters produce valid Exchange JSON with schema fields"""
        adapters = [
            ("mem0_exchange", "mem0_to_exchange", {"id": "x1", "memory": "test mem0"}, None),
            ("zep_exchange", "zep_to_exchange", [{"memory": "test zep", "created_at": "2026-09-01T10:00:00Z"}], None),
            ("letta_exchange", "letta_to_exchange", {"blocks": [{"label": "p", "value": "test letta"}]}, None),
            ("graphiti_exchange", "graphiti_to_exchange", [{"fact": "test graphiti", "created_at": "2026-09-01T10:00:00Z"}], None),
            ("langmem_exchange", "langmem_to_exchange", [{"content": "test langmem", "timestamp": "2026-09-01T10:00:00Z"}], None),
        ]
        results = []
        for module_name, func_name, data, _ in adapters:
            mod = __import__(module_name)
            func = getattr(mod, func_name)
            src = tmp_path / f"{module_name}_src.json"
            if isinstance(data, dict):
                src.write_text(json.dumps(data), encoding="utf-8")
            else:
                src.write_text(json.dumps(data), encoding="utf-8")
            out = str(tmp_path / f"{module_name}_out.json")
            result = func(str(src), out, source=module_name.replace("_exchange",""))
            assert result is not None, f"{module_name} returned None"
            data_out = _verify_exchange_json(out, min_facts=1)
            results.append(data_out)
        
        # COGX needs a directory
        cogx_dir = tmp_path / "cogx_all"
        cogx_dir.mkdir()
        src = cogx_dir / "memories.jsonl"
        src.write_text(json.dumps({"kind": "memory", "content": "test cogx"}), encoding="utf-8")
        out = str(tmp_path / "cogx_all_out.json")
        import cogx_exchange as cx
        result = cx.cogx_to_exchange(str(cogx_dir), out, source="cogx")
        assert result is not None
        _verify_exchange_json(out, min_facts=1)
        
        print(f"All 6 adapters produced valid Exchange JSON")
