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

HUB = os.path.dirname(os.path.abspath(__file__))
# 每条：目录 / ONNX 相对路径 / 备注
MODELS = {
    'msmarco': dict(dir='cross_encoder', onnx='model.onnx',
                    note='英文 MS MARCO，中文实测更差（Top1 73%→53%），别用'),
    'bge': dict(dir='bge_reranker', onnx='model.onnx',
                note='BAAI/bge-reranker-base fp32，中文佳，1.11GB，冷启动约 6.3s'),
    'bge-int8': dict(dir='bge_reranker_int8', onnx='onnx/model_quantized.onnx',
                     note='同上的 int8 量化版，266MB（小 4 倍），加载 1.34s，实测 60/62'),
}
# ★2026-09-15 实测：冷启动总成本里**精排反而比 embedding 大** ——
#   精排 1061MB vs embedding 543MB，而两者每个短进程都要重载一次。
#   hard_bench 62 题实测（逐模型，同一份卷子）：
#     bge      fp32  1061MB  加载 2.35s  59/62 = 95.2%
#     bge-int8 int8   266MB  加载 1.34s  60/62 = 96.8%   ← 选它
#   打分几乎一致（[2.35,-7.23,-3.76] vs [2.38,-7.56,-3.67]）。
#   ★60 vs 59 只差 1 题、远在噪声内 → 采用 int8 的**真正理由是体积 1/4、
#     加载快 1.75 倍**，不是"它更准"。别把噪声讲成结论（同 embedding 那次）。
DEFAULT_MODEL = os.environ.get('MEM_RERANK_MODEL') or 'bge-int8'

_cache = {}
_load_sec = {}


def _paths(model):
    m = MODELS[model]
    d = os.path.join(HUB, 'models', m['dir'])
    return d, os.path.join(d, m['onnx'])


def _load(model=None):
    model = model or DEFAULT_MODEL
    if model in _cache:
        return _cache[model]
    import time
    import onnxruntime as ort
    from tokenizers import Tokenizer
    t0 = time.time()
    d, onnx_path = _paths(model)
    tok = Tokenizer.from_file(os.path.join(d, 'tokenizer.json'))
    # ★沿用原写法：精排输入本来就被截断到 128 token，固定 pad 到 128 影响不大
    #   （与 embedding 那边的坑不同——那边是 2-token 短句被撑到 512，才慢 73 倍）。
    tok.enable_padding(length=128, pad_id=0, pad_token='[PAD]')
    tok.enable_truncation(max_length=128)
    sess = ort.InferenceSession(onnx_path, providers=['CPUExecutionProvider'])
    names = {i.name for i in sess.get_inputs()}
    _cache[model] = (tok, sess, names)
    _load_sec[model] = time.time() - t0
    return _cache[model]


def info(model=None):
    """自检：模型路径、文件大小、本次加载耗时（供评分卡/诊断使用）。"""
    model = model or DEFAULT_MODEL
    d, onnx_path = _paths(model)
    ok = os.path.exists(onnx_path) and os.path.exists(os.path.join(d, 'tokenizer.json'))
    r = {'model': model, 'dir': d, 'available': ok,
         'size_mb': round(os.path.getsize(onnx_path) / 1048576, 1) if os.path.exists(onnx_path) else None,
         'loaded': model in _cache,
         'load_sec': round(_load_sec.get(model, 0.0), 2),
         'note': MODELS[model]['note']}
    return r


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
