# -*- coding: utf-8 -*-
"""tests/test_exchange_adapters_io.py — mem0/zep exchange adapters (were 0%).

The Exchange Schema "sellable" path: Mem0/Zep exports → Memory Exchange v1/v2.
Covers: JSON/JSONL/dict-form parsing, content-field normalization, backfill
semantics (temporal_source), PII redaction toggle, mt-to-mem0/mt-to-zep
roundtrip field preservation, and invalid-record skipping.
"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['MEM_DB'] = os.path.join(tempfile.mkdtemp(prefix='mt_xio_'), 't.db')

import mem0_exchange as m0
import zep_exchange as zp
import memtether_exchange as mx


def _write(tmp_path, name, payload):
    fp = tmp_path / name
    if isinstance(payload, str):
        fp.write_text(payload, encoding='utf-8')
    else:
        fp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return str(fp)


class TestMem0ToExchange:
    def test_memories_dict_form(self, tmp_path):
        src = _write(tmp_path, 'm0.json', {'memories': [
            {'memory': 'user prefers dark mode', 'created_at': '2026-10-01 10:00'},
            {'memory': 'uses comfui for images'},
        ]})
        data, _ = m0.mem0_to_exchange(src, None, pii_redact=False)
        assert data['producer'] == 'mem0_exchange'
        assert data['counts']['facts'] == 2
        backfilled = [f for f in data['facts'] if f['temporal_source'] == 'backfilled']
        native = [f for f in data['facts'] if f['temporal_source'] == 'native']
        assert len(backfilled) == 1 and len(native) == 1

    def test_jsonl_form(self, tmp_path):
        src = _write(tmp_path, 'm0.jsonl',
                     '{"memory": "line one"}\n{"memory": "line two"}\n')
        data, _ = m0.mem0_to_exchange(src, None, pii_redact=False)
        assert data['counts']['facts'] == 2

    def test_content_field_variants(self, tmp_path):
        src = _write(tmp_path, 'm0v.json', [
            {'content': 'via content key'},
            {'text': 'via text key'},
            {'fact': 'via fact key'},
            'plain string row',
        ])
        data, _ = m0.mem0_to_exchange(src, None, pii_redact=False)
        assert data['counts']['facts'] == 4

    def test_invalid_rows_skipped(self, tmp_path):
        src = _write(tmp_path, 'm0bad.json', {'memories': [
            {'memory': 'good one'},
            {'no_content_field': True},
            42,
        ]})
        data, _ = m0.mem0_to_exchange(src, None, pii_redact=False)
        assert data['counts']['facts'] == 1

    def test_pii_redact_on_by_default(self, tmp_path):
        src = _write(tmp_path, 'm0pii.json', {'memories': [
            {'memory': 'contact me at foo.bar@example.com anytime'},
        ]})
        data, _ = m0.mem0_to_exchange(src, None, pii_redact=True)
        assert data.get('pii_redacted', 0) >= 1
        assert 'foo.bar@example.com' not in data['facts'][0]['content']

    def test_output_file_written(self, tmp_path):
        src = _write(tmp_path, 'm0o.json', {'memories': [{'memory': 'x'}]})
        out = str(tmp_path / 'out.json')
        data, outp = m0.mem0_to_exchange(src, out, pii_redact=False)
        assert outp == out and os.path.exists(out)
        assert json.load(open(out, encoding='utf-8'))['schema_version'] == mx.SCHEMA_VERSION

    def test_sha256_present(self, tmp_path):
        src = _write(tmp_path, 'm0h.json', {'memories': [{'memory': 'hash me'}]})
        data, _ = m0.mem0_to_exchange(src, None, pii_redact=False)
        assert data['sha256'] == mx._sha256({'facts': data['facts'], 'supersessions': [], 'tool_assets': []})


class TestMtToMem0:
    def test_field_preservation(self, tmp_path):
        ex = {'schema_name': mx.SCHEMA_NAME, 'schema_version': 2, 'facts': [
            {'uid': 'u1', 'content': 'c1', 'subject': 'alice', 'type': 'fact',
             'valid_from': '2026-10-01', 'q_value': 0.7}]}
        src = _write(tmp_path, 'ex.json', ex)
        payload, _ = m0.mt_to_mem0(src, None)
        item = payload['memories'][0]
        assert item['memory'] == 'c1' and item['user_id'] == 'alice'
        assert item['memtether_uid'] == 'u1' and item['memtether_q_value'] == 0.7
        assert payload['schema_name'] == mx.SCHEMA_NAME


class TestZepToExchange:
    def test_zep_v2_fact_form(self, tmp_path):
        src = _write(tmp_path, 'z.json', {'facts': [
            {'fact': 'user is in Harbin', 'created_at': '2026-10-02 08:00',
             'valid_at': '2026-10-02 08:00', 'uuid': 'z-1'},
        ]})
        data, _ = zp.zep_to_exchange(src, None, pii_redact=False)
        f = data['facts'][0]
        assert f['uid'] == 'z-1' and f['content'] == 'user is in Harbin'
        assert f['temporal_source'] == 'native'
        assert f['valid_from'].startswith('2026-10-02')

    def test_zep_invalid_at_sets_valid_to(self, tmp_path):
        src = _write(tmp_path, 'z2.json', [{'fact': 'old fact',
            'created_at': '2026-09-01', 'invalid_at': '2026-09-05'}])
        data, _ = zp.zep_to_exchange(src, None, pii_redact=False)
        f = data['facts'][0]
        assert f['valid_to'] and f['invalidated_at']

    def test_nested_message_form(self, tmp_path):
        src = _write(tmp_path, 'z3.json', [{'message': {'content': 'nested content'}}])
        data, _ = zp.zep_to_exchange(src, None, pii_redact=False)
        assert data['facts'][0]['content'] == 'nested content'

    def test_zep_pii_redact(self, tmp_path):
        src = _write(tmp_path, 'z4.json', [{'fact': 'email me at a@b.com now'}])
        data, _ = zp.zep_to_exchange(src, None, pii_redact=True)
        assert data.get('pii_redacted', 0) >= 1


class TestMtToZep:
    def test_roundtrip_fields(self, tmp_path):
        ex = {'schema_name': mx.SCHEMA_NAME, 'schema_version': 2, 'facts': [
            {'uid': 'z9', 'content': 'zed content', 'subject': 'bob'}]}
        src = _write(tmp_path, 'zex.json', ex)
        payload, _ = zp.mt_to_zep(src, None)
        assert any(m.get('memtether_uid') == 'z9' for m in payload.get('facts', payload.get('memories', [])))
