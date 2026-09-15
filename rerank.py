# -*- coding: utf-8 -*-
"""rerank.py — cross-encoder 精排（复刻 agentmemory V4 的核心增益项，但换掉它的模型）

为什么需要它：RRF 只用"各路排名"，对"查询措辞与记忆原文不一致"的情况无能为力。
cross-encoder 把 (query, doc) 拼成一个序列做全注意力，能读出双塔相似度看不出的相关性。

为什么不用 torch：本机 huggingface.co 直连不通（实测 000），hf-mirror.com 可达；
.venv-memory 已有 onnxruntime 1.30 + tokenizers 0.23 → 走 ONNX，省掉 torch 约 2GB 下载。

★模型选择的实测教训（2026-09-15）：
  agentmemory V4 用 cross-encoder/ms-marco-MiniLM-L-6-v2（英文 MS MARCO 篇章），
  在我的中文技术碎片记忆上**反而把 Top1 从 73% 拉到 53%** —— 它是为英文自然段落训的，
  倾向把"表述自然"的条目排前，而把"含精确术语但生硬"的真正答案压下去。
  → 中文场景改用 BAAI/bge-reranker-base，并把两者都做成可切换以便实测对比。
"""
import os
import numpy as np

HUB = r'E:\RUANJIAN\memory_hub'
MODELS = {
    'msmarco': os.path.join(HUB, 'models', 'cross_encoder'),
    'bge': os.path.join(HUB, 'models', 'bge_reranker'),
}
DEFAULT_MODEL = 'msmarco'

_cache = {}


def _load(model=None):
    model = model or DEFAULT_MODEL
    if model in _cache:
        return _cache[model]
    import onnxruntime as ort
    from tokenizers import Tokenizer
    d = MODELS[model]
    tok = Tokenizer.from_file(os.path.join(d, 'tokenizer.json'))
    tok.enable_padding(length=128, pad_id=0, pad_token='[PAD]')
    tok.enable_truncation(max_length=128)
    sess = ort.InferenceSession(os.path.join(d, 'model.onnx'),
                                providers=['CPUExecutionProvider'])
    names = {i.name for i in sess.get_inputs()}
    _cache[model] = (tok, sess, names)
    return _cache[model]


def available(model=None):
    try:
        _load(model)
        return True
    except Exception as e:
        print('[warn] 重排器不可用:', str(e)[:80])
        return False


def scores(query, docs, model=None):
    """返回每条 doc 与 query 的相关性 logits（越大越相关）。"""
    if not docs:
        return []
    tok, sess, names = _load(model)
    encs = [tok.encode(query, d) for d in docs]
    feed = {'input_ids': np.array([e.ids for e in encs], dtype=np.int64),
            'attention_mask': np.array([e.attention_mask for e in encs], dtype=np.int64)}
    if 'token_type_ids' in names:
        feed['token_type_ids'] = np.array([e.type_ids for e in encs], dtype=np.int64)
    out = np.asarray(sess.run(None, feed)[0]).reshape(len(docs), -1)
    return (out[:, 0] if out.shape[1] > 1 else out[:, -1]).tolist()


def normalize(xs):
    """min-max 归一到 0~1（候选集内部相对排序，避免 logits 跨查询不可比）。"""
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    if hi - lo < 1e-9:
        return [1.0] * len(xs)
    return [(x - lo) / (hi - lo) for x in xs]
