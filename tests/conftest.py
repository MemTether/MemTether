"""Shared fixtures for MemTether test suite."""
import os
import tempfile
import sqlite3
import pytest

# ★2026-10-19：把 tempfile 的默认目录钉到系统临时目录。
#   实测（2026-10-19 全面体检）：tests/ 下有 13 处
#   `tempfile.mkdtemp(prefix='mt_*')` **没有传 dir=**，而本机 TMPDIR/TEMP
#   被指到仓库根 → 127 个 mt_*/qvalue_test_* 目录全部落在仓库根，
#   既污染 git status，又让 .gitignore 规则空转（check-ignore rc=1）。
#   根因修法：在 conftest（所有测试 import 前最早执行）设 TMPDIR，
#   一处修全部生效，不需要逐个改 13 个文件。
#   判据：跑完 pytest 后仓库根不应新增任何 mt_*/qvalue_test_* 目录。
if not os.environ.get('MEMTETHER_TMPDIR_OVERRIDE'):
    _sys_tmp = os.environ.get('TMPDIR') or os.environ.get('TMP') or os.environ.get('TEMP')
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 只有当 TMPDIR 指向仓库内（异常态）时才纠正；否则尊重系统设置
    if _sys_tmp and os.path.abspath(_sys_tmp).startswith(os.path.abspath(_repo_root)):
        _safe = os.path.join(os.environ.get('SYSTEMROOT', r'C:\Windows'), 'Temp')
        os.environ['TMPDIR'] = _safe
        os.environ['TMP'] = _safe
        os.environ['TEMP'] = _safe
        tempfile.tempdir = _safe
        os.environ['MEMTETHER_TMPDIR_OVERRIDE'] = '1'


# ★2026-10-19：生产库防污染护栏（autouse）。
#
# 事故背景（本轮真实发生）：我在写 tests/test_profile.py 时用了
#   monkeypatch.setenv("MEM_DB", ...)
# 以为这样就隔离了。但 gateway 在 **import 时**就把 DB 路径定成模块级常量，
# 之后再设环境变量已经晚了 → 38 条测试探针（"fact number 0..29" 等）
# 全部写进生产库 memory.db，直到 preflight 才被发现。
#
# 根因不是"某个测试忘了隔离"，而是**没有任何机制在测试连到生产库时报警**。
# 这个 autouse fixture 就是那道机制：任何测试（含未来新增的）只要让
# gateway.DB / governance.DB 指到已知生产库路径，就直接 fail-fast，
# 并把原因写清楚 —— 而不是静默污染。
#
# ★判据（受控演练过）：故意不设 MEM_DB 跑一个 remember，本 fixture 必须报错。
_PROD_DB_MARKERS = (
    'memory_hub' + os.sep + 'memory.db',
    'memory_hub/memory.db',
)


def _looks_like_prod(db_path):
    if not db_path:
        return False
    norm = os.path.normcase(os.path.normpath(str(db_path)))
    return any(m in norm for m in _PROD_DB_MARKERS)


@pytest.fixture(autouse=True)
def _guard_against_production_db(tmp_path):
    """Fail fast if a test points the engine at the production DB.

    ★2026-10-19 升级为"事前隔离 + 事后复核"两段：
      第一版只在 teardown 检查，受控演练发现 `1 passed, 1 error` ——
      测试本身已经 pass、**写入已经发生**，护栏只是事后报警，污染已造成。
      现在：
        1) 事前：若引擎模块的 DB 常量已指向生产库，立刻把它改到一个
           每测试独立的临时库，让写入落在沙箱里；
        2) 事后：再复查一次，若仍指向生产库 → 直接 AssertionError。
      这样即使测试作者完全忘了隔离，也不会污染生产库。
    """
    import sys as _sys
    _names = ('gateway', 'governance', 'memsearch', 'consolidation')
    mods = [m for name, m in list(_sys.modules.items())
            if name in _names and m is not None]

    # 1) 事前：把指向生产库的模块改到沙箱
    _sandbox = str(tmp_path / 'guard_sandbox.db')
    _relocated = []
    for m in mods:
        if _looks_like_prod(getattr(m, 'DB', None)):
            try:
                m.DB = _sandbox
                _relocated.append(m.__name__)
                if hasattr(m, 'init_db'):
                    m.init_db()
            except Exception:
                pass
    # ★2026-10-20 修（实测不稳定）：原版设了 MEM_DB 就**从不清理**，
    #   而 tests/test_cross_process_writes.py 用 subprocess.Popen 起子进程、
    #   继承父进程环境 → 子进程连到已消失的 guard_sandbox.db，
    #   表现为 4-writer 并发测试偶发失败（stash 掉本 conftest 后 615 全过）。
    #   修法：记住原值，teardown 时**如实恢复**（删掉本来没有的键）。
    _had_mem_db = 'MEM_DB' in os.environ
    _old_mem_db = os.environ.get('MEM_DB')
    if _relocated and not _had_mem_db:
        os.environ['MEM_DB'] = _sandbox

    try:
        yield
    finally:
        if not _had_mem_db:
            os.environ.pop('MEM_DB', None)
        else:
            os.environ['MEM_DB'] = _old_mem_db

    # 2) 事后：仍指向生产库 → 失败（防止将来有人绕过事前隔离）
    bad = [(m.__name__, getattr(m, 'DB', None)) for m in mods
           if _looks_like_prod(getattr(m, 'DB', None))]
    if bad:
        raise AssertionError(
            '测试把引擎指向了生产库 memory.db：%r。\n'
            '根因：gateway 在 import 时就把 DB 定为模块常量，monkeypatch.setenv '
            '太晚。正确做法见 tests/test_gateway_deep.py::_setup —— '
            '先 os.environ["MEM_DB"]=tmp 再 importlib.reload(gateway)。' % (bad,))


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary MemTether database with the standard schema."""
    db_path = str(tmp_path / "test_memory.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE,
            type TEXT,
            subject TEXT,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            superseded_by TEXT,
            valid_from TEXT, valid_to TEXT,
            recorded_at TEXT, invalidated_at TEXT,
            temporal_source TEXT,
            source TEXT, scope TEXT DEFAULT 'shared',
            confidence REAL DEFAULT 0.8, tags TEXT,
            created_at TEXT, updated_at TEXT,
            q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0,
            predicate TEXT
        );
        CREATE TABLE IF NOT EXISTS tool_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE, name TEXT, aliases TEXT,
            type TEXT, status TEXT DEFAULT 'active',
            path TEXT, entrypoint TEXT, capabilities TEXT,
            recipe_ids TEXT, known_failures TEXT, prerequisites TEXT,
            last_verified_at TEXT, verification_method TEXT,
            source TEXT, created_at TEXT, updated_at TEXT,
            q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS supersessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_uid TEXT, new_uid TEXT, reason TEXT,
            by_agent TEXT, ts TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def sample_fact(tmp_db):
    """Insert a sample fact and return its uid."""
    conn = sqlite3.connect(tmp_db)
    uid = "test-fact-001"
    conn.execute(
        "INSERT INTO facts (uid, content, type, source, recorded_at, valid_from) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, "This is a test memory about Python 3.12", "fact", "test", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    )
    conn.commit()
    conn.close()
    return uid

import os as _os_bk
import tempfile as _tf_bk

_bk = _os_bk.path.join(_tf_bk.mkdtemp(prefix='mt_bk_'), 'backups')
_os_bk.makedirs(_bk, exist_ok=True)
_os_bk.environ.setdefault('MEMTETHER_BACKUP_HOME', _bk)
