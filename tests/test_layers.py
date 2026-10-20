# -*- coding: utf-8 -*-
"""tests/test_layers.py -- L0/L1/L2 layered recall (2026-10-19).

Motivation (capability audit against OpenViking 40.3K stars, 2026-10-19):
OpenViking differentiates with a virtual filesystem where every directory
carries a generated abstract (L0) / overview (L1) / full content (L2), so an
agent scans summaries before opening the source. MemTether previously returned
the whole `content` for every hit, which wastes the injection slot and gives the
agent no cheap way to judge relevance.

This locks the deterministic, zero-LLM, zero-new-table implementation:
  L0 = first sentence (after stripping a leading 【...】 tag)
  L1 = first N sentences (default 3)
  L2 = full text (unchanged, byte-identical to legacy behaviour)

Hard contracts asserted here:
  1. layer="L2" (the default) leaves `content` byte-identical to legacy.
  2. L0 is strictly shorter than L1, which is strictly shorter than L2, for
     multi-sentence content.
  3. Every result carries layers metadata {L0, L1, L2_len} at every layer,
     so a caller can escalate depth without a second round trip.
  4. Deterministic: same input -> same layers, forever.
  5. Leading 【tag】 is stripped from L0/L1 but preserved in L2.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memsearch


LONG = ("【项目】MemTether 是跨客户端 AI 记忆中枢。"
        "它用 SQLite 做唯一真源，不依赖云服务。"
        "纠错时不删除旧值，而是 supersession 链。"
        "双时间轴让 as-of 查询能回答当时系统知道什么。"
        "第五条句子里有 hash-chain 审计。"
        "第六条句子里有 Q-Value 强化学习。"
        "第七条句子里有冲突检测双通道。")


class TestContentLayers:
    def test_l0_is_first_sentence_without_tag(self):
        lay = memsearch.content_layers(LONG)
        assert lay["L0"] == "MemTether 是跨客户端 AI 记忆中枢"  # 首句不含句读（更干净的结论行）
        assert "【" not in lay["L0"]

    def test_l1_is_first_three_sentences(self):
        lay = memsearch.content_layers(LONG, l1_sentences=3)
        assert lay["L1"].count("。") == 3
        assert "supersession 链" in lay["L1"]
        assert "hash-chain" not in lay["L1"]

    def test_l2_is_full_text_unchanged(self):
        lay = memsearch.content_layers(LONG)
        assert lay["L2"] == LONG

    def test_monotonic_lengths(self):
        lay = memsearch.content_layers(LONG)
        assert len(lay["L0"]) < len(lay["L1"]) < len(lay["L2"])

    def test_deterministic(self):
        a = memsearch.content_layers(LONG)
        b = memsearch.content_layers(LONG)
        assert a == b

    def test_single_sentence_content_does_not_explode(self):
        lay = memsearch.content_layers("只有一句话没有句号")
        assert lay["L0"] and lay["L2"] == "只有一句话没有句号"

    def test_empty_content_is_graceful(self):
        lay = memsearch.content_layers("")
        assert lay["L0"] == "(空)"
        assert lay["L2"] == ""

    def test_l0_cap_applies(self):
        long_first = "啊" * 400 + "。后面的内容。"
        lay = memsearch.content_layers(long_first, l0_cap=50)
        assert len(lay["L0"]) <= 51  # 50 + ellipsis
        assert lay["L0"].endswith("…")


class TestApplyLayers:
    def _rows(self):
        return [{"uid": "u1", "content": LONG, "type": "fact", "score": 1.0}]

    def test_l2_default_is_byte_identical(self):
        rows = self._rows()
        out = memsearch.apply_layers(rows, layer="L2")
        assert out[0]["content"] == LONG
        assert out[0]["layers"]["L2_len"] == len(LONG)

    def test_l0_replaces_content_and_keeps_meta(self):
        out = memsearch.apply_layers(self._rows(), layer="L0")
        assert out[0]["content"] == "MemTether 是跨客户端 AI 记忆中枢"
        assert out[0]["layers"]["L1"]
        assert out[0]["layers"]["L2_len"] == len(LONG)

    def test_l1_replaces_content(self):
        out = memsearch.apply_layers(self._rows(), layer="L1")
        assert out[0]["content"].count("。") == 3

    def test_does_not_mutate_input(self):
        rows = self._rows()
        before = rows[0]["content"]
        memsearch.apply_layers(rows, layer="L0")
        assert rows[0]["content"] == before

    def test_empty_list(self):
        assert memsearch.apply_layers([], layer="L0") == []

    def test_unknown_layer_falls_back_to_l2(self):
        out = memsearch.apply_layers(self._rows(), layer="L9")
        assert out[0]["content"] == LONG


class TestSearchHybridLayerParam:
    """search_hybrid must accept and honour the layer kwarg."""

    def test_signature_has_layer(self):
        import inspect
        sig = inspect.signature(memsearch.search_hybrid)
        assert "layer" in sig.parameters
        assert sig.parameters["layer"].default == "L2"
        assert "l1_sentences" in sig.parameters


class TestMetaPrefixStripping:
    """L0 must spend its budget on the conclusion, not on `标题(日期,source=x)：`.

    Hard contract (tightened 2026-10-19 after the first version over-stripped):
      * A leading parenthesised block is treated as metadata and dropped.
      * Without a parenthesised block, NOTHING is stripped — topic words must
        survive, because L0's whole job is "one line is enough to judge relevance".
    """

    def test_paren_meta_prefix_dropped(self):
        t = "全面体检第2轮11项发现(2026-10-19,source=codex)：A两仓漂移5处已同步。"
        lay = memsearch.content_layers(t)
        assert lay["L0"].startswith("A两仓漂移5处")
        assert "source=codex" not in lay["L0"]

    def test_fullwidth_paren_meta_prefix_dropped(self):
        t = "标题（2026-10-19，source=codex）：结论在这里。"
        lay = memsearch.content_layers(t)
        assert lay["L0"] == "结论在这里"

    def test_no_paren_block_is_not_stripped(self):
        t = "注意：这个冒号不应该被剥掉，因为它在句中。"
        lay = memsearch.content_layers(t)
        assert lay["L0"].startswith("注意")

    def test_tagged_headline_keeps_topic_word(self):
        t = "【标签】普通结论：后面的内容。"
        lay = memsearch.content_layers(t)
        assert "普通结论" in lay["L0"]

    def test_l2_is_never_stripped(self):
        t = "标题(2026-10-19,source=codex)：结论。"
        lay = memsearch.content_layers(t)
        assert lay["L2"] == t

    def test_idempotent(self):
        t = "标题(2026-10-19,source=codex)：结论在这里。"
        once = memsearch._strip_meta_prefix(t)
        assert memsearch._strip_meta_prefix(once) == once
