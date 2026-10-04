# -*- coding: utf-8 -*-
"""memtether_paths.py — 本机路径解析单点（2026-09-25）

背景：发布库 memtether/ 与生产库 memory_hub/ 同源异码。
本仓 memsearch.py / gateway.py 的默认 DB = 本目录 memory.db（发布库占位库，64KB 空库），
而真实记忆在生产中枢目录下。若不显式切库，
调用方会**静默检索空库**并返回空结果 —— 「跑起来不报错、但结果错」那一类。

★同一份解析逻辑被 harness / export 等多处需要。按本仓教训
  「同一件事存两处必然漂移」，这里做**单点**，其它文件 import 本模块，
  不再各写一份。

解析顺序（fail-safe，不抛异常）：
  1) $MEM_DB            —— 显式指定，最高优先（测试/切库用）
  2) $MEM_HUB_DIR/memory.db
  3) local_paths.json(hub_dir)/memory.db   —— 本机专有，gitignored
  4) 本目录 memory.db   —— 开源克隆形态，无生产库时保持原行为

`local_paths.json` 被 .gitignore 排除：文件缺失时自动降级，绝不报假错。
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))


def resolve_hub_dir():
    """解析生产记忆中枢目录；找不到返回 None（开源克隆形态）。"""
    env_hub = (os.environ.get('MEM_HUB_DIR') or '').strip()
    if env_hub:
        return env_hub
    try:
        with open(os.path.join(HERE, 'local_paths.json'), encoding='utf-8') as f:
            cfg = json.load(f)
        hub = (cfg.get('hub_dir') or '').strip()
        if hub:
            return hub
    except (OSError, ValueError):
        pass
    return None


def default_db():
    """默认真源库路径。不修改环境变量。"""
    if os.environ.get('MEM_DB'):
        return os.environ['MEM_DB']
    hub = resolve_hub_dir()
    if hub:
        cand = os.path.join(hub, 'memory.db')
        if os.path.exists(cand):
            return cand
    return os.path.join(HERE, 'memory.db')


def default_store(db_path=None):
    """默认向量库路径（与真源库同目录，保证同步切换）。"""
    if os.environ.get('MEM_STORE'):
        return os.environ['MEM_STORE']
    db = db_path or default_db()
    return os.path.join(os.path.dirname(db), 'mem0_store')


def ensure_env():
    """把解析结果写进环境变量，供 memsearch 等「导入时读 env」的模块使用。

    ★必须在 `import memsearch` 之前调用。
    返回 (db_path, store_path)。
    """
    db = default_db()
    os.environ.setdefault('MEM_DB', db)
    store = default_store(db)
    if os.path.isdir(store):
        os.environ.setdefault('MEM_STORE', store)
    return db, os.environ.get('MEM_STORE')
