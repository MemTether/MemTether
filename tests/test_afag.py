# -*- coding: utf-8 -*-
"""E-AFAG answer-formulation hints (T-B, 2026-10-05)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memsearch import extract_answer_hints


class TestAFAG:
    def test_counting_hint(self):
        results = [
            {'content': 'used ComfyUI for image 1', 'updated_at': '2026-01-01'},
            {'content': 'used ComfyUI for image 2', 'updated_at': '2026-01-02'},
        ]
        h = extract_answer_hints('counting', 'how many times ComfyUI', results)
        assert h and 'COUNT ComfyUI: 2' in h

    def test_temporal_hint(self):
        results = [
            {'content': 'old fact', 'updated_at': '2026-01-01 10:00:00'},
            {'content': 'new fact', 'updated_at': '2026-06-01 10:00:00'},
        ]
        h = extract_answer_hints('temporal', 'when did things change', results)
        assert h and 'EARLIEST: 2026-01-01' in h and 'LATEST: 2026-06-01' in h

    def test_knowledge_update_latest(self):
        results = [
            {'content': 'PORT 8080 is used by server', 'updated_at': '2026-01-01'},
            {'content': 'PORT 9090 is now the server', 'updated_at': '2026-06-01'},
        ]
        h = extract_answer_hints('knowledge-update', 'current server port', results)
        assert h and 'LATEST PORT' in h and '9090' in h

    def test_no_hints_single_session(self):
        results = [{'content': 'plain fact', 'updated_at': '2026-01-01'}]
        h = extract_answer_hints('single-session', 'plain query', results)
        assert h is None
