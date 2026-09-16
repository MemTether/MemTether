# -*- coding: utf-8 -*-
"""embed_local.py — 本地 embedding（ONNX），给记忆中枢做语义兜底

【为什么做这个 —— 2026-09-15 的真实事故】
  智谱 embedding-3 返回 `429 code=1113 余额不足或无可用资源包,请充值`，
  Astra 欠费、DeepSeek 官方 key 失效 —— **三条外部通道同时挂掉**。
  后果：向量索引 326 条完好，但**查询侧无法 embed**，每次检索都降级成纯关键词。
  实测查「微信装在哪」不再返回资产条目（纯关键词匹配不到"装在哪"的语义）。

  → 语义能力 100% 依赖外部付费通道，没有一个字节的本地兜底。这是结构性弱点。
  → 本模块补上它：**永不断供、免费、数据不出本机**。

【为什么走 ONNX 而不是 torch】
  与 rerank.py 同样的理由：本机 huggingface.co 直连 502，只有 hf-mirror 可达；
  .venv-memory 已有 onnxruntime + tokenizers，走 ONNX 可省掉 torch 约 2GB 下载。

【多模型支持】
  暴露多个 profile，便于用**同一份评测集**客观对比（而不是凭感觉选模型）。
  用环境变量 MEM_EMBED_MODEL 切换，默认 …见 DEFAULT_MODEL。

【实测踩坑记录】
  1) 固定 padding 是性能杀手。ONNX 会按**实际张量形状**前向，
     `enable_padding(length=512)` 会让「预热」这种 2 token 的短句也按 512 前向：
     实测 32 条要 36.7 秒（1146 ms/条）。改成**批内动态 padding** 后 15.6 ms/条，快 73 倍。
  2) 必须用 `encode_batch()` 而不是逐条 `encode()`：
     逐条时每条各自成批（batch=1），拼起来长度不齐 → numpy 直接报
     inhomogeneous shape；而且逐条前向本身更慢。
  3) ONNX 模型加载后**必须 warmup**：首次推理含内存分配与 kernel 选择。
  4) 图优化缓存（optimized_model_filepath）**没有帮助**：实测加载优化图 3.70s
     vs 原始 3.24s，反而略慢，且多出 1.7GB 磁盘文件。
     → 冷启动的成本是**权重从磁盘读入**，不是图优化。（已删除该文件）
  5) 反斜杠 w（word char 元字符）在 Python 正则里会匹配中文 —— 与治理层同一个坑。
     本模块正则一律显式限定 ASCII 字符集；注释里也写成 raw string 或双反斜杠，
     否则会触发 SyntaxWarning。
"""

import os
import sys
import time

HUB = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 模型档案
# 每条：目录名, ONNX 相对路径, 输出维度, 显示名, 备注
MODELS = {
    'bge-m3': dict(
        dir='bge_m3', onnx='onnx/model_fp16.onnx', dim=1024,
        name='BAAI/bge-m3 (fp16)', note='多语言多粒度，质量最好，体积最大'),
    'bge-m3-int8': dict(
        dir='bge_m3', onnx='onnx/model_quantized.onnx', dim=1024,
        name='BAAI/bge-m3 (int8)', note='int8 量化，体积减半，CPU 上通常更快'),
    'bge-small-zh': dict(
        dir='bge_small_zh', onnx='onnx/model_fp16.onnx', dim=512,
        name='BAAI/bge-small-zh-v1.5', note='中文专用，45MB，冷启动快'),
    'bge-base-zh': dict(
        dir='bge_base_zh', onnx='onnx/model_fp16.onnx', dim=768,
        name='BAAI/bge-base-zh-v1.5', note='中文专用，中等体积'),
}

DEFAULT_MODEL = 'bge-m3-int8'
# ★默认模型是**实测选出来的**，不是拍的（2026-09-15，hard_bench 62 题的改述式难题集）：
#     bge-m3-int8   58/62 = 93.5%   543MB   冷启动 4.1s   16.8 ms/条
#     bge-m3 fp16   56/62 = 90.3%  1081MB   冷启动 7.2s   26.3 ms/条   ← 反而更差
#     bge-small-zh  55/62 = 88.7%    45MB   冷启动 0.7s    1.4 ms/条
#   反直觉点：**int8 量化版打败了 fp16 原版**。合理解释是 CPU 上 fp16 并无原生
#   指令，ORT 需转换/模拟，反而引入精度损失；而 int8 量化有轻微正则化效果。
#   不过 58 vs 56 只差 2 题，落在抽样噪声内（n=62 时 95%CI 约 ±7pp），
#   所以选择 int8 的真正理由是**同档质量下体积减半、冷启动快 1.8 倍**，
#   而不是"它更准"。—— 别把噪声讲成结论。
MAX_LEN = 512             # 截断上限（记忆条目通常远短于此）

_sess = None
_tok = None
_active = None            # 当前已加载的 profile key
_load_sec = 0.0

# ★ONNX fp16 对 provider 敏感：CPUExecutionProvider 对 fp16 支持有限，
#   优先 CUDA（本机 onnxruntime 是 CPU 版，会自然回退）。
_PROVIDERS = [
    ('CUDAExecutionProvider', 'CPUExecutionProvider'),
    ('CPUExecutionProvider',),
]


def _profile(key=None):
    key = key or os.environ.get('MEM_EMBED_MODEL') or DEFAULT_MODEL
    if key not in MODELS:
        raise KeyError('未知模型 %r，可选：%s' % (key, ', '.join(MODELS)))
    p = dict(MODELS[key])
    p['key'] = key
    p['onnx_path'] = os.path.join(HUB, 'models', p['dir'], p['onnx'])
    p['tok_path'] = os.path.join(HUB, 'models', p['dir'], 'tokenizer.json')
    return p


def dim(key=None):
    return _profile(key)['dim']


def available(key=None):
    """模型文件是否就位（不触发加载）。"""
    p = _profile(key)
    return os.path.exists(p['onnx_path']) and os.path.exists(p['tok_path'])


def _load(key=None):
    """加载 ONNX 会话（进程内缓存）。返回 (tokenizer, session)。"""
    global _sess, _tok, _load_sec, _active
    p = _profile(key)
    if _sess is not None and _active == p['key']:
        return _tok, _sess
    if _sess is not None and _active != p['key']:
        _sess = _tok = None          # 换模型 → 丢弃旧会话

    import onnxruntime as ort
    from tokenizers import Tokenizer

    t0 = time.time()
    _tok = Tokenizer.from_file(p['tok_path'])
    _tok.enable_truncation(max_length=MAX_LEN)
    # ★绝不写 enable_padding(length=512)：见模块头「踩坑 1」。动态 padding 即可。
    _tok.enable_padding()

    last_err = None
    for provs in _PROVIDERS:
        try:
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = 0        # 0 = 交给 ORT 自己决定
            _sess = ort.InferenceSession(p['onnx_path'], so, providers=list(provs))
            break
        except Exception as e:
            last_err = e
            continue
    if _sess is None:
        raise RuntimeError('ONNX 会话创建失败: %s' % last_err)

    _active = p['key']
    _load_sec = time.time() - t0
    try:
        _warmup()
    except Exception:
        pass
    return _tok, _sess


def _warmup():
    """预热：首次推理含 kernel 选择与内存分配，不预热会让首次查询莫名变慢。"""
    import numpy as np
    tok, sess = _tok, _sess
    enc = tok.encode('预热')
    feed = {
        'input_ids': np.array([enc.ids], dtype=np.int64),
        'attention_mask': np.array([enc.attention_mask], dtype=np.int64),
    }
    names = {i.name for i in sess.get_inputs()}
    if 'token_type_ids' in names:
        feed['token_type_ids'] = np.array([enc.type_ids], dtype=np.int64)
    sess.run(None, feed)


def encode(texts, batch=16, key=None):
    """把文本编码成向量（已做 L2 归一化，可直接用余弦相似度）。

    返回 list[list[float]]，维度见 _profile()['dim']。
    """
    import numpy as np
    if not texts:
        return []
    tok, sess = _load(key)
    names = {i.name for i in sess.get_inputs()}
    out = []
    for i in range(0, len(texts), batch):
        chunk = [t if t else ' ' for t in texts[i:i + batch]]
        # ★必须 encode_batch：见模块头「踩坑 2」
        encs = tok.encode_batch(chunk)
        feed = {
            'input_ids': np.array([e.ids for e in encs], dtype=np.int64),
            'attention_mask': np.array([e.attention_mask for e in encs], dtype=np.int64),
        }
        if 'token_type_ids' in names:
            feed['token_type_ids'] = np.array([e.type_ids for e in encs], dtype=np.int64)
        res = sess.run(None, feed)
        vec = _pick_embedding(res, feed, len(chunk))
        n = np.linalg.norm(vec, axis=1, keepdims=True)
        vec = vec / np.clip(n, 1e-12, None)
        out.extend(vec.astype(np.float32).tolist())
    return out


def _pick_embedding(res, feed, n_text):
    """从 ONNX 输出里认出 embedding 张量。

    不同导出命名/形状不一致（已池化 vs last_hidden_state），按形状推断更稳：
      - (n, dim)  → 直接是句向量
      - (n, seq, dim) → 用 attention_mask 做 mean pooling
    """
    import numpy as np
    want = _profile()['dim']
    for r in res:
        a = np.asarray(r)
        if a.ndim == 2 and a.shape[1] == want:
            return a
    for r in res:
        a = np.asarray(r)
        if a.ndim == 3:
            m = np.asarray(feed['attention_mask'], dtype=np.float32)[..., None]
            return (a * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
    for r in res:
        a = np.asarray(r)
        if a.ndim == 2 and a.shape[0] == n_text:
            return a
    raise RuntimeError('无法从 ONNX 输出中识别 embedding 张量：%s'
                       % [np.asarray(r).shape for r in res])


def info(key=None):
    """自检信息。"""
    p = _profile(key)
    ok = available(p['key'])
    d = {'model': p['name'], 'key': p['key'], 'dim': p['dim'],
         'dir': p['dir'], 'available': ok,
         'loaded': _sess is not None and _active == p['key'],
         'load_sec': round(_load_sec, 2)}
    if ok:
        d['size_mb'] = round(os.path.getsize(p['onnx_path']) / 1048576, 1)
    return d


def list_models():
    out = []
    for k, v in MODELS.items():
        e = dict(v)
        e['key'] = k
        e['available'] = available(k)
        sub = os.path.join(HUB, 'models', v['dir'], v['onnx'])
        e['size_mb'] = round(os.path.getsize(sub) / 1048576, 1) if os.path.exists(sub) else None
        out.append(e)
    return out


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true', help='列出所有模型档案')
    ap.add_argument('--info', action='store_true')
    ap.add_argument('--model', default=None, help='指定模型 key')
    ap.add_argument('--bench', action='store_true', help='实测加载与编码耗时')
    ap.add_argument('--sim', nargs=3, metavar=('Q', 'A', 'B'),
                    help='比较查询与两条文本的相似度')
    ap.add_argument('--encode', default=None)
    a = ap.parse_args()

    if a.list:
        print('%-14s %-26s %-5s %-9s %-8s %s' % ('key', 'model', 'dim', 'size', '就绪', '备注'))
        print('-' * 96)
        for m in list_models():
            print('%-14s %-26s %-5d %-9s %-8s %s' % (
                m['key'], m['name'], m['dim'],
                ('%.1fMB' % m['size_mb']) if m['size_mb'] else '-',
                'OK' if m['available'] else '缺失', m['note']))
        sys.exit(0)

    if a.info:
        import json
        print(json.dumps(info(a.model), ensure_ascii=False, indent=2))

    if a.bench:
        p = _profile(a.model)
        print('模型: %s (dim=%d)' % (p['name'], p['dim']))
        print('就绪: %s' % available(p['key']))
        if not available(p['key']):
            print('模型文件缺失'); sys.exit(1)
        t0 = time.time(); _load(a.model)
        print('  首次加载+预热:  %.2f 秒' % (time.time() - t0))
        t0 = time.time(); encode(['第二次调用'], key=a.model)
        print('  二次调用:       %.3f 秒（已缓存会话）' % (time.time() - t0))
        t0 = time.time(); vs = encode(['批量测试文本 %d' % i for i in range(32)], key=a.model)
        el = time.time() - t0
        print('  编码 32 条:     %.3f 秒（%.1f ms/条）' % (el, el * 1000 / 32))
        print('  向量维度:       %d' % len(vs[0]))

    if a.sim:
        q, x, y = a.sim
        vs = encode([q, x, y], key=a.model)
        dot = lambda p, r: sum(i * j for i, j in zip(p, r))
        print('查询: %s' % q)
        print('  vs %-40s %.4f' % (x[:38], dot(vs[0], vs[1])))
        print('  vs %-40s %.4f' % (y[:38], dot(vs[0], vs[2])))

    if a.encode:
        v = encode([a.encode], key=a.model)[0]
        print('维度 %d，前 5 值: %s' % (len(v), [round(x, 4) for x in v[:5]]))
