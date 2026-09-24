# -*- coding: utf-8 -*-
"""hubguard.py — 多客户端并发治理：单锁 · 版本检测 · 原子写 · 共享文件守卫

为什么需要它（2026-09-17 实测取证）：
    同一个记忆中枢被两个客户端同时用（国内版 workbuddy + 国际版 workbuddy_ai）。
    实测三条硬事实：
      ① `gateway.py` 里 BEGIN IMMEDIATE / busy_timeout 之外**零并发原语** ——
         没有跨进程锁，两个客户端同时写 = 后写覆盖前写，**无检测、无拒绝、无通知**。
         现场例证：06:16:49~06:17:04 另一侧连写 5 条并重建投影，本侧全程不知道。
      ② `facts` 表**有** source（447 条零缺失，8 个来源），但投影 `MEMORY.md`
         逐条**不带 source** → 看投影分不清哪条是谁写的。
      ③ 工作区 `memory/` 与另一客户端**同一物理文件**（目录联接），
         且投影 `rebuild` 是**整体重写** → 两边互相覆盖。

本模块给这三条各配一个**可验证**的机制，并且**不要求改任何调用方的函数体**：

    ① 单锁      hub_lock()        —— 跨进程独占锁（msvcrt/flock），全仓**同一把**
                                    （gateway.py / mem.py 共用，才谈得上互斥）
    ② 版本检测  DBWatch           —— 基于 SQLite `PRAGMA data_version`：
                                    "我读之后别人提交过没有"，可检出、可报错
    ③ 原子写    atomic_write()    —— 临时文件 + os.replace，读者永远看不到半截投影
       + 守卫    commit_guarded() —— 重写共享文件前比对指纹，变了就**拒写**而不是覆盖

★设计原则（每一条都是踩过的坑换来的）：
  1. **不侵入**：`install_guards()` 用包装器接管任意版本的 gateway.py，
     所以 72550 B 与 60632 B 两版**都适用**，不需要 diff 对齐。
  2. **不静默**：所有"本该报警"的路径都抛异常或返回显式字段，
     绝不"跑起来不报错、但结果全错"。
  3. **不抢改全局**：`journal_mode=WAL` 是**库级持久**设置，会改变另一侧脚本
     对 memory.db 的假设（`cp memory.db` 会丢掉 -wal 里的提交）→
     默认**不动**，只在 `MEM_DB_JOURNAL=wal` 时显式开启，并在 status 里报出来。
  4. **失败要降级得看得见**：`os.replace` 在 Windows 上可能因目标被占用而失败；
     此时回落到就地写，并在返回值里标 `atomic=False` + 落 stderr，不假装成功。

用法：
  python hubguard.py status                     # 锁状态 + 库/投影状态 + 来源分布
  python hubguard.py doctor                     # 全面体检（槽位/落后/共享 inode/锁/侧车文件）
  python hubguard.py gen                        # 打印库指纹（可跨进程比较）
  python hubguard.py lock -- <命令...>          # 在锁保护下跑命令
  python hubguard.py annotate [--dry-run]       # 给**别人的**投影补来源标记（幂等/可回退）
  python hubguard.py journal [--tail 20]        # 记录或查看中枢变更日志（含归属）
  python hubguard.py selftest                   # 证明锁真的独占（不是"跑通了"）
  python hubguard.py snapshot <路径>            # 打指纹
  python hubguard.py verify <路径> --sha256 X   # 校验指纹（变了退出码 1）

依赖：仅标准库。
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

HUB = os.path.dirname(os.path.abspath(__file__))

# ============================================================================
# 路径与配置
# ============================================================================

_DB_CACHE = {}


def _looks_like_hub(p):
    """是不是一个记忆中枢库：能只读打开、且有 `facts` 表。"""
    if not os.path.isfile(p):
        return False
    try:
        uri = 'file:%s?mode=ro' % p.replace('\\', '/').replace('?', '%3F').replace('#', '%23')
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            r = conn.execute("SELECT 1 FROM sqlite_master "
                             "WHERE type='table' AND name='facts'").fetchone()
            if not r:
                return False
            # ★P0-11 (2026-09-24): 0 行空壳库不算 hub（曾把 65KB/0 行的
            #   memtether/memory.db 误认为真源库，锁加错地方）
            try:
                n = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                return n > 0
            except Exception:
                return False
        finally:
            conn.close()
    except Exception:
        return False


def _discover_db():
    """在常见位置找真源库。返回 (path, origin) 或 (None, None)。

    ★为什么需要发现：本模块要能同时在这两个地方跑 ——
      ① 部署目录 `memory_hub/`（库就在旁边，`HUB/memory.db` 直接命中）；
      ② 发布仓 `memtether/`（带 gateway.py 但 **不带** memory.db，被 .gitignore 忽略）。
      写死 `HUB/memory.db` 会让在发布仓里跑 `status` 变成"库不存在、结论全是 None"，
      等于体检工具在最需要它的场景里瞎了。
    ★发现的库一律标 `origin='discovered'` 并打印告警 —— 绝不静默换库：
      守错库比不守更坏（锁加到 A 库、写入落在 B 库）。
    """
    home = os.path.expanduser('~')
    cands = [
        (os.path.join(HUB, 'memory.db'), 'hub'),
        (os.path.join(os.path.dirname(HUB), 'memory_hub', 'memory.db'), 'sibling'),
        (os.path.join(os.getcwd(), 'memory.db'), 'cwd'),
        (os.path.join(home, 'memory_hub', 'memory.db'), 'home'),
        (os.path.join(home, '.agents', 'memory', 'memory.db'), 'agents'),
    ]
    for p, o in cands:
        if _looks_like_hub(p):
            return os.path.abspath(p), o
    return None, None


def db_path(explicit=None, explain=False):
    """真源库路径。**与 gateway.py / mem.py / memsearch.py 同一套 MEM_DB 规则**。

    解析顺序：显式参数 → MEM_DB → HUB/memory.db → 发现（见 _discover_db）。
    前两级与 gateway.py 完全一致；只有当前两级都落空（发布仓场景）才启用发现，
    且发现结果会被标出来。explain=True 时返回 (path, origin)。
    """
    key = explicit or os.environ.get('MEM_DB') or ''
    hit = _DB_CACHE.get(key)
    if hit is None:
        if explicit or os.environ.get('MEM_DB'):
            p = explicit or os.environ.get('MEM_DB')
            origin = 'explicit' if explicit else 'env:MEM_DB'
            if not os.path.isabs(p):
                p = os.path.join(HUB, p)
        else:
            p = os.path.join(HUB, 'memory.db')
            origin = 'hub'
            # ★P0-11 (2026-09-24): 空壳库判据——存在但 facts 0 行 → 跳过走发现链
            if not os.path.exists(p) or not _looks_like_hub(p):
                d, o = _discover_db()
                if d:
                    p, origin = d, 'discovered:' + o
        hit = (p, origin)
        _DB_CACHE[key] = hit
    return hit if explain else hit[0]


def db_origin(explicit=None):
    return db_path(explicit, explain=True)[1]


def lock_path(db=None):
    """★全仓**唯一**那把锁。放在库旁边 → 换 MEM_DB 就自动换锁（演练可完全隔离）。"""
    return os.environ.get('MEM_LOCK') or (db_path(db) + '.hub.lock')


def holder_path(lp):
    return lp + '.holder'


def journal_path(db=None):
    return os.environ.get('MEM_JOURNAL') or (db_path(db) + '.journal.jsonl')


def proj_paths():
    """投影目标。默认与 gateway.rebuild 一致；MEM_PROJ_PATH 可用 os.pathsep 覆盖多个。

    ★为什么需要覆盖：原实现把 `~/.workbuddy/MEMORY.md` 写死在 rebuild 里，
      于是"想验证 rebuild 的改动"就**必然**要动线上投影 —— 等于不能安全地测。
      加了这个开关，演练才能写到临时目录、对线上零影响。
    """
    v = os.environ.get('MEM_PROJ_PATH')
    if v:
        return [x for x in v.split(os.pathsep) if x]
    return [os.path.expanduser(os.path.join('~', '.workbuddy', 'MEMORY.md'))]


def proj_budget():
    return int(os.environ.get('MEM_PROJ_BUDGET', '3980'))


def workspace_memory_paths():
    """两侧工作区的记忆文件（用于共享 inode 体检）。取最新若干个。"""
    import glob
    home = os.path.expanduser('~')
    out = []
    for pat in (os.path.join(home, 'WorkBuddy', '*', '.workbuddy', 'memory', 'MEMORY.md'),
                os.path.join(home, 'WorkBuddy AI', '*', '.workbuddy-ai', 'memory', 'MEMORY.md')):
        out.extend(sorted(glob.glob(pat)))
    return out


def slot_budget(path):
    """这个共享槽位的**字符硬顶**。

    ★两个数不一样、绝不能混用（实测 2026-09-16）：
      用户级 `~/.workbuddy/MEMORY.md`（国际版 `~/.workbuddy-ai/MEMORY.md`）
        → 官方约 4000，中枢侧再自留 20 字符余量 → `proj_budget()`（默认 3980）。
      工作区级 `<工作区>/.workbuddy[-ai]/memory/MEMORY.md` → 8000。

    ★判据是**路径形状**而不是"谁在调用"：同一份物理文件会被两个客户端各自注入，
      预算取的是**槽位**的属性，与调用方无关。按调用方判会得到"同一个文件两个预算"。

    ★这两个数都是**硬截断**（不是截末尾、是整篇丢弃/截断），越线是静默的
      —— 所以本函数只用来"报余量"，绝不用来"自动裁剪"。
    """
    if os.sep + 'Work' in path:
        return int(os.environ.get('MEM_WS_BUDGET', '8000'))
    return proj_budget()


# ============================================================================
# ① 跨进程独占锁
# ============================================================================

class HubBusy(TimeoutError):
    """等锁超时。带上"谁在占"的信息，让调用方直接能报出来。"""

    def __init__(self, path, holder=None, waited=0.0):
        self.path = path
        self.holder = holder or {}
        self.waited = waited
        who = ''
        if self.holder:
            who = '（持有者 pid=%s agent=%s 自 %s，用途：%s）' % (
                self.holder.get('pid'), self.holder.get('agent'),
                self.holder.get('since'), self.holder.get('purpose') or '-')
        super().__init__('获取记忆中枢锁超时，已等 %.1fs：%s%s' % (waited, path, who))


class ConcurrentModification(RuntimeError):
    """共享文件在"我读过之后、要写之前"被别人改过。**拒写**，不覆盖。"""

    def __init__(self, path, before, after, hint=''):
        self.path = path
        self.before = before or {}
        self.after = after or {}
        super().__init__(
            '检测到并发修改，已拒写：%s\n'
            '  我读到时：sha256=%s size=%s\n'
            '  现在实际：sha256=%s size=%s\n'
            '%s'
            % (path,
               str(self.before.get('sha256'))[:16], self.before.get('size'),
               str(self.after.get('sha256'))[:16], self.after.get('size'),
               ('  ' + hint + '\n') if hint else ''))


def _os_lock(fh):
    if os.name == 'nt':
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fh):
    if os.name == 'nt':
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def pid_alive(pid):
    """判断进程是否还活着。

    ★绝对不要用 `os.kill(pid, 0)` —— 在 Windows 上 Python 的 os.kill 是
      TerminateProcess 的封装，signal 0 会**直接把对方进程杀掉**。
    """
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            SYNCHRONIZE = 0x00100000
            h = k32.OpenProcess(SYNCHRONIZE, False, pid)
            if not h:
                return False
            try:
                return k32.WaitForSingleObject(wintypes.HANDLE(h), 0) == 0x00000102  # WAIT_TIMEOUT
            finally:
                k32.CloseHandle(wintypes.HANDLE(h))
        except Exception:
            return True                      # 判不出来就当作活着（保守：不抢锁）
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True


def read_holder(lp):
    try:
        with open(holder_path(lp), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_holder(lp, info):
    try:
        atomic_write(holder_path(lp), json.dumps(info, ensure_ascii=False))
    except Exception:
        pass


_MTX = threading.Lock()
_HELD = {}          # lock_path -> {'fh', 'depth', 'token'}
_RLOCKS = {}        # lock_path -> threading.RLock（同进程跨线程串行 + 同线程可重入）


def _rlock_for(lp):
    with _MTX:
        rl = _RLOCKS.get(lp)
        if rl is None:
            rl = threading.RLock()
            _RLOCKS[lp] = rl
        return rl


def lock_acquire(timeout=None, agent=None, purpose='', db=None):
    """拿锁。同进程同线程可重入（depth 计数），同进程不同线程串行，跨进程靠 OS 锁。"""
    lp = lock_path(db)
    if timeout is None:
        timeout = float(os.environ.get('MEM_LOCK_TIMEOUT', '20'))
    agent = agent or os.environ.get('MEM_AGENT') or 'unknown'
    rl = _rlock_for(lp)
    if not rl.acquire(timeout=max(0.1, timeout)):
        raise HubBusy(lp, read_holder(lp), timeout)

    with _MTX:
        st = _HELD.get(lp)
        if st is not None:                    # 本进程已持有 → 只加计数
            st['depth'] += 1
            return {'path': lp, 'reentrant': True, 'depth': st['depth'], 'token': st['token']}

    d = os.path.dirname(lp)
    if d:
        os.makedirs(d, exist_ok=True)
    fh = open(lp, 'a+b')
    t0 = time.time()
    while True:
        try:
            _os_lock(fh)
            break
        except OSError:
            if time.time() - t0 >= timeout:
                fh.close()
                rl.release()
                raise HubBusy(lp, read_holder(lp), time.time() - t0)
            time.sleep(0.05)

    token = '%d-%s' % (os.getpid(),
                       hashlib.md5(('%f' % time.time()).encode()).hexdigest()[:8])
    info = {'pid': os.getpid(), 'agent': agent, 'purpose': purpose,
            'since': time.strftime('%Y-%m-%d %H:%M:%S'),
            'waited': round(time.time() - t0, 3), 'token': token}
    with _MTX:
        _HELD[lp] = {'fh': fh, 'depth': 1, 'token': token}
    _write_holder(lp, info)
    return {'path': lp, 'reentrant': False, 'depth': 1, 'token': token, 'waited': info['waited']}


def lock_release(lp):
    with _MTX:
        st = _HELD.get(lp)
        if st is None:
            return False
        st['depth'] -= 1
        last = st['depth'] <= 0
        if last:
            _HELD.pop(lp, None)
    if last:
        # ★只有 token 对得上才删，否则会把后来者的 holder 信息误删（A 释放、B 已抢到）
        cur = read_holder(lp)
        if cur.get('token') == st['token']:
            try:
                os.unlink(holder_path(lp))
            except Exception:
                pass
        try:
            _os_unlock(st['fh'])
        except Exception:
            pass
        try:
            st['fh'].close()
        except Exception:
            pass
    _rlock_for(lp).release()
    return last


class _LockCtx(object):
    def __init__(self, timeout, agent, purpose, db):
        self.args = (timeout, agent, purpose, db)
        self.info = None

    def __enter__(self):
        self.info = lock_acquire(*self.args)
        return self.info

    def __exit__(self, *exc):
        lock_release(self.info['path'])
        return False


def hub_lock(timeout=None, agent=None, purpose='', db=None):
    """with hub_lock(): ...  —— 全仓唯一那把锁。"""
    return _LockCtx(timeout, agent, purpose, db)


def lock_status(db=None):
    """当前锁状态（只读探测：尝试非阻塞抢一次，抢到即说明没人占）。

    ★只读命令**不允许**创建文件：锁文件不存在 ⇒ 必然没人持有 ⇒ 直接返回。
      否则"看一眼状态"就会在被守仓库里留下一个未跟踪文件（实测把对方的
      `git status` 从 `M gateway.py` 变成 `M gateway.py` + `?? memory.db.hub.lock`）。
    """
    lp = lock_path(db)
    out = {'path': lp, 'exists': os.path.exists(lp), 'held': False,
           'holder': read_holder(lp), 'stale_holder': False}
    if not out['exists']:
        return out
    try:
        fh = open(lp, 'a+b')
    except Exception as e:
        out['error'] = str(e)
        return out
    try:
        _os_lock(fh)
        _os_unlock(fh)
        out['held'] = False
        if out['holder']:
            h = out['holder']
            out['stale_holder'] = not pid_alive(h.get('pid'))
    except OSError:
        out['held'] = True
    finally:
        fh.close()
    return out


# ============================================================================
# ② 版本检测（SQLite data_version）
# ============================================================================

class DBWatch(object):
    """「我读之后别人提交过没有」。

    ★为什么用 `PRAGMA data_version`：它是 SQLite **专为这件事**设计的 ——
      同一个连接两次读到的值不同，当且仅当**别的连接**在中间提交过。
      比"看 mtime"精确（mtime 会被无关的读放大、也可能因缓存不更新），
      比"整库 hash"便宜（712 KB 每次哈希约 1ms，但库一大就不可接受）。
    """

    def __init__(self, db=None):
        self.path = db_path(db)
        self.conn = None
        self.v0 = None
        self.stat0 = None
        self.error = None
        try:
            if os.path.exists(self.path):
                self.conn = sqlite3.connect(self.path, timeout=3)
                self.v0 = self.conn.execute('PRAGMA data_version').fetchone()[0]
                st = os.stat(self.path)
                self.stat0 = (st.st_mtime_ns, st.st_size)
        except Exception as e:
            self.error = str(e)

    def changed(self):
        """返回 (bool, 详情 dict)。任何一路信号变了都算变了。"""
        d = {'data_version': None, 'mtime_ns': None, 'size': None,
             'v_changed': False, 'stat_changed': False}
        if self.conn is not None:
            try:
                d['data_version'] = self.conn.execute('PRAGMA data_version').fetchone()[0]
                d['v_changed'] = (d['data_version'] != self.v0)
            except Exception as e:
                d['error'] = str(e)
        try:
            st = os.stat(self.path)
            d['mtime_ns'], d['size'] = st.st_mtime_ns, st.st_size
            d['stat_changed'] = (d['mtime_ns'], d['size']) != self.stat0
        except Exception:
            pass
        return bool(d['v_changed'] or d['stat_changed']), d

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None


def db_fingerprint(db=None):
    """**可跨进程比较**的库指纹：任何进程读同一状态都得到同一个值。

    （DBWatch 的 data_version 是"连接内相对量"，不能跨进程比；
      这里给一个绝对量，供 journal / 巡检消费。）
    """
    p, origin = db_path(db, explain=True)
    out = {'path': p, 'origin': origin, 'exists': os.path.exists(p)}
    if not out['exists']:
        return out
    st = os.stat(p)
    out.update(size=st.st_size, mtime_ns=st.st_mtime_ns,
               mtime=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)))
    try:
        conn = sqlite3.connect(p, timeout=3)
        conn.row_factory = sqlite3.Row
        out['journal_mode'] = conn.execute('PRAGMA journal_mode').fetchone()[0]
        for tbl in ('facts', 'tool_assets', 'audit_log', 'supersessions'):
            try:
                r = conn.execute('SELECT COUNT(*) n, COALESCE(MAX(id),0) mx FROM %s' % tbl).fetchone()
                out[tbl] = {'n': r['n'], 'max_id': r['mx']}
            except Exception:
                pass
        try:
            r = conn.execute('SELECT source, MAX(created_at) mx FROM facts '
                             'GROUP BY source ORDER BY mx DESC').fetchall()
            out['sources'] = [(x['source'], x['mx']) for x in r]
        except Exception:
            pass
        conn.close()
    except Exception as e:
        out['error'] = str(e)
    out['fp'] = hashlib.sha256(json.dumps(
        [out.get('size'), out.get('mtime_ns'),
         out.get('facts', {}).get('n'), out.get('facts', {}).get('max_id'),
         out.get('audit_log', {}).get('max_id')],
        sort_keys=True).encode()).hexdigest()[:16]
    return out


# ============================================================================
# ③ 原子写 / 共享文件守卫
# ============================================================================

def _detect_newline(path):
    """读出目标现有的换行风格。

    ★为什么必须做：Python 里 `open(p,'w')` 默认 `newline=None`，会把 '\\n' **翻译成
      os.linesep**（Windows 上 = CRLF）；而 `newline=''` 则一个字节都不翻译。
      原 gateway.rebuild 用的是前者 → 线上投影是 **CRLF**。
      如果守卫改用 `newline=''` 写回，同一个字符串落盘会从 CRLF 变成 LF：
      内容没变、字节全变 —— 这正是"跑起来不报错、但文件被悄悄改了"的典型。
      所以这里**跟随目标现状**：CRLF 就写 CRLF，LF 就写 LF。
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(65536)
    except Exception:
        return '\r\n' if os.name == 'nt' else '\n'
    if b'\r\n' in head:
        return '\r\n'
    return '\n'


def detect_newline(path):
    """公开入口：跟随目标文件现有的换行风格（`'\\r\\n'` 或 `'\\n'`）。

    ★为什么要有公开别名（2026-09-17）：`slot_update` / `wslog_append` / `atomic_write`
      三处都要做"换行跟随"。规则一旦有第二份实现，就迟早出现"同一个文件、两个工具、
      两种换行"——内容没变、字节全变，属最典型的静默失败。规则本体在 `_detect_newline`
      （前 64KB 里出现 CRLF 就用 CRLF），本函数只是把它变成可被其它模块复用的公开 API。

    ★目标不存在/读不到时返回 `os.linesep` 的归一形式（Windows = `'\\r\\n'`）。
      需要"文件不存在时由调用方决定"的语义（如 `wslog_append` 的 CLI），
      请调用方自己先判 `exists()`。
    """
    return _detect_newline(path)


def atomic_write(path, text, encoding='utf-8', fsync=True, newline=None):
    """临时文件 + os.replace。

    ★三个 Windows 特有的坑，都做了显式降级而不是假装成功：
      1) 目标被别的进程以"不共享删除"的方式打开时，`os.replace` 会 PermissionError
         —— 回落到就地写，并标 `atomic=False`（调用方必须把这个字段报出去）。
      2) 目标有**硬链接**（st_nlink > 1）时，`os.replace` 会换掉 inode，
         让别的链接**静默指向旧文件** —— 检测到就改成就地写，并标 `hardlink=True`。
      3) 目标是**符号链接**时（双版本互通就靠它），`os.replace` 会把链接本身
         替换成普通文件，互通被静默打断 —— 先解析到 realpath 再原子替换，
         返回体带 `symlink=True / real=`。
    ★newline 默认 None = 跟随目标现状（见 _detect_newline），不要随手改成 ''。
      newline=None 时本函数会**先把 text 的行尾统一成 LF、再展开成目标风格**，
      因此传进来的 text 是 LF 还是已含 CRLF 都无所谓（不会出现 '\r\r\n'）。
      newline 显式给值时按 Python 原生语义翻译，调用方自负其责。
    """
    path = os.path.abspath(path)
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    if newline is None:
        newline = _detect_newline(path)
        # ★归一化：调用方给的文本可能是 LF，也可能是**已含 CRLF** 的。
        #   先把行尾统一成 LF，再按目标既有风格展开，最后用 newline='' 写入（不再翻译）。
        #   若直接把探测到的 '\r\n' 交给 fdopen，已含 CRLF 的文本会被**二次翻译**
        #   成 '\r\r\n' —— 内容看着没错、字节全变，是最典型的静默失败。
        #   （2026-09-17 实测：slot_update 接入后，"CRLF 跟随"用例就是这么挂的。）
        #   注：只把 '\r\n' 折成 '\n'，**不碰孤立 '\r'** —— 原生语义从不改写孤立 CR，
        #   能不改的字节就不改（纯 LF / 纯 CRLF 输入下本步是恒等变换）。
        text = text.replace('\r\n', '\n')
        if newline == '\r\n':
            text = text.replace('\n', '\r\n')
        write_nl = ''
    else:
        write_nl = newline
    # ★★符号链接必须在硬链接检查**之前**处理：
    #   `~/.workbuddy-ai/MEMORY.md` 是指向 `~/.workbuddy/MEMORY.md` 的符号链接
    #   （双版本互通就靠它）。os.stat 会跟随链接 → nlink 看起来是 1，
    #   于是走到 os.replace(tmp, path) → **把符号链接替换成普通文件**，
    #   两个客户端从此各写各的、互通被静默打断。
    #   正确做法：解析到真实路径后在那里做原子替换 —— 原子性与链接都保住。
    if os.path.islink(path):
        real = os.path.realpath(path)
        if os.path.abspath(real) != path:
            # ★递归时必须传 write_nl 而不是 newline：text 在上面已经被展开成目标
            #   风格了，再交给 fdopen 翻译一次就会变成 '\r\r\n'。
            r = atomic_write(real, text, encoding=encoding, fsync=fsync,
                             newline=write_nl)
            r.update(symlink=True, real=real, path=path)
            return r
    nlink = 0
    try:
        nlink = os.stat(path).st_nlink
    except Exception:
        pass
    if nlink > 1:
        r = _write_inplace(path, text, encoding, write_nl)
        r.update(atomic=False, hardlink=True,
                 why='目标有 %d 个硬链接，os.replace 会断开链接 → 改成就地写' % nlink)
        sys.stderr.write('[hubguard][warn] %s：%s\n' % (path, r['why']))
        return r
    fd, tmp = tempfile.mkstemp(prefix='.hg-', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline=write_nl) as f:
            f.write(text)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.replace(tmp, path)
        return {'ok': True, 'path': path, 'bytes': os.path.getsize(path),
                'atomic': True, 'newline': repr(newline)}
    except PermissionError as e:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        r = _write_inplace(path, text, encoding, write_nl)
        r.update(atomic=False, why='os.replace 被拒（%s）→ 就地写' % e.__class__.__name__)
        sys.stderr.write('[hubguard][warn] %s：%s\n' % (path, r['why']))
        return r
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _write_inplace(path, text, encoding, newline):
    """就地写（无法原子替换时的降级路径）。

    ★契约：这里的 `newline` 必须是 **已解析** 的写入参数，而不是 None/探测值 ——
      调用方（atomic_write）在 newline=None 时已经把 text 归一化并按目标风格展开，
      传进来的是 `''`（不翻译）。本函数**不再做任何换行推断**，否则会二次翻译。
    """
    with open(path, 'w', encoding=encoding, newline=newline) as f:
        f.write(text)
    return {'ok': True, 'path': path, 'bytes': os.path.getsize(path),
            'atomic': False, 'newline': repr(newline)}


def snapshot(path):
    """打指纹：内容 sha256 + 尺寸 + mtime + inode + 链接数。"""
    path = os.path.abspath(path)
    try:
        st = os.stat(path)
        with open(path, 'rb') as f:
            data = f.read()
        return {'path': path, 'exists': True, 'size': st.st_size, 'nlink': st.st_nlink,
                'mtime_ns': st.st_mtime_ns, 'st_ino': st.st_ino, 'st_dev': st.st_dev,
                'sha256': hashlib.sha256(data).hexdigest(),
                'chars': len(data.decode('utf-8', 'replace'))}
    except FileNotFoundError:
        return {'path': path, 'exists': False, 'size': 0, 'sha256': None, 'chars': 0}
    except Exception as e:
        return {'path': path, 'exists': os.path.exists(path), 'error': str(e)}


def same_snapshot(a, b):
    return bool(a) and bool(b) and a.get('exists') == b.get('exists') \
        and a.get('sha256') == b.get('sha256')


def commit_guarded(path, text, before, on_conflict='abort', tag=''):
    """把 `text` 写进共享文件，但**先证明"我读到的还是现在这个"**。

    before      —— 读之前 snapshot() 的结果
    on_conflict —— abort（默认，抛异常）/ force（覆盖，但把对方版本另存）/ sidecar（不碰原文件，我的版本另存）
    """
    cur = snapshot(path)
    if not same_snapshot(before, cur):
        hint = ('提示：另一客户端在你读取之后改过这个文件。'
                '请重新读取后再决定，或显式选 force/sidecar。')
        if on_conflict == 'abort':
            raise ConcurrentModification(path, before, cur, hint)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        if on_conflict == 'sidecar':
            side = '%s.mine-%s' % (path, stamp)
            atomic_write(side, text)
            return {'ok': False, 'conflict': True, 'wrote': side,
                    'kept': path, 'before': before, 'after': cur}
        side = '%s.other-%s' % (path, stamp)
        try:
            with open(path, encoding='utf-8') as f:
                atomic_write(side, f.read())
        except Exception:
            pass
        r = atomic_write(path, text)
        r.update(conflict=True, saved_other=side, before=before, after=cur)
        return r
    r = atomic_write(path, text)
    r.update(conflict=False, before=before, after=cur)
    return r


def _win_append(path, data, retries=30, sleep_base=0.002, sleep_max=0.05):
    """Windows 原生原子追加：CreateFileW(FILE_APPEND_DATA) + WriteFile。

    ★为什么不能用 `os.open(..., O_APPEND)` + `os.write`：
      Windows **没有** POSIX 那种"VFS 层原子追加"。CRT 的 `_O_APPEND` 是
      "先 seek 到文件末尾、再 write" 两步，两个进程可以同时 seek 到同一偏移，
      然后互相覆盖。实测（`%TEMP%\\append_probe.py`，2 进程 × 400 行，四轮）：

          os.open(O_APPEND) : 795 / 799 / 796 / 796 行   ← 丢 1~5 行
          FILE_APPEND_DATA  : 800 / 800 / 800 / 800 行   ← 一行不丢

      **且丢的是整行、存活行全部完好** —— 只看内容根本发现不了，是最典型的静默失败。
      （同一探针第一轮曾 0 丢失：间歇性发作，所以"跑一次没事"不能作为证据。）

      FILE_APPEND_DATA 打开时，系统保证"定位到末尾 + 写"是一个原子动作。

    ★为什么必须重试（2026-09-17 从 wslog_append.atomic_append 收编，勿删）：
      实测 8 进程并发各自 CreateFileW 时会**偶发打开失败**（安全软件/索引器短暂持锁）。
      单次失败就抛出 = 把一次瞬时争用变成**永久丢数据**；演练里的表现是
      「正好丢掉某个进程的一整帧（50 行）」，而不是丢几行。
    ★重试的边界（fail-closed，别放宽）：**只有"打开失败"才重试**。
      一旦拿到句柄后 WriteFile 失败，立即抛出、绝不重试 —— 因为 Windows 在
      WriteFile 返回 FALSE 时并不保证 lpNumberOfBytesWritten 有效，我们无法知道
      已落了多少字节；按 done 续写或按 0 重发**都可能产出重复内容**（长度对得上、
      格式也对，只是多了半条），比丢行难发现得多。
      而真实故障形态恰恰是"打开就失败"（安全软件/索引器短暂持锁），
      所以这条边界不影响主要保护效果 —— 见 `_probe_append_retry()` 的确定性验证。
    ★FILE_APPEND_DATA 下每次打开都从**当时的 EOF** 开始写，所以重试重发整段
      在"未写入任何字节"时天然正确（不可能覆盖别人已追加的内容）。
    """
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    CreateFileW = k32.CreateFileW
    CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p]
    CreateFileW.restype = ctypes.c_void_p
    WriteFile = k32.WriteFile
    WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    WriteFile.restype = wintypes.BOOL
    CloseHandle = k32.CloseHandle

    FILE_APPEND_DATA = 0x0004
    SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
    OPEN_ALWAYS = 4
    ATTR_NORMAL = 0x80
    INVALID = ctypes.c_void_p(-1).value

    done = 0
    attempts = max(1, int(retries))
    last = 'unknown'
    for attempt in range(attempts):
        h = CreateFileW(str(path), FILE_APPEND_DATA, SHARE_ALL, None,
                        OPEN_ALWAYS, ATTR_NORMAL, None)
        if h is None or h == INVALID:
            last = 'CreateFileW 失败 err=%d' % ctypes.get_last_error()
            if attempt + 1 < attempts:
                time.sleep(min(sleep_base * (attempt + 1), sleep_max))
            continue
        try:
            buf = ctypes.create_string_buffer(data, len(data))
            while done < len(data):
                w = wintypes.DWORD(0)
                if not WriteFile(h, ctypes.byref(buf, done), len(data) - done,
                                 ctypes.byref(w), None):
                    # ★已拿到句柄、写失败 → **立即抛出，绝不重试**。
                    #   WriteFile 返回 FALSE 时 Windows 不保证 lpNumberOfBytesWritten 有效，
                    #   我们无法知道到底落了多少字节：按 done 续写可能**重复**，
                    #   按 0 重发整段同样可能**重复**。两种猜法都会产出"长度对得上、
                    #   格式也对、就是多了半条"的脏数据 —— 比丢行难发现得多。
                    #   所以 fail-closed：让调用方看见异常，自己决定怎么处置。
                    raise IOError('原子追加写入失败（err=%d）：已写入 %d/%d 字节 -> %s；'
                                  '已获得句柄后写失败，无法确定已落盘字节数，'
                                  '拒绝重试以免内容重复'
                                  % (ctypes.get_last_error(), done, len(data), path))
                if w.value == 0:
                    raise IOError('WriteFile 写入 0 字节 -> %s' % path)
                done += w.value
        finally:
            CloseHandle(ctypes.c_void_p(h))
        return done
    raise IOError('原子追加失败（重试 %d 次均无法打开文件）：%s -> %s'
                  % (attempts, last, path))


def _probe_append_retry():
    """**确定性**证明 `_win_append` 的重试语义（不靠"多跑几次看运气"）。

    做法：临时把 `ctypes.WinDLL` 换成假 kernel32，注入两种真实故障：
      ① 打开失败 K 次 —— 模拟安全软件/索引器短暂持锁（这是线上真实故障形态）
      ② 写了一半就返回失败 —— 模拟部分落盘后报错
    期望：
      ① 重试后**成功**，且内容**只出现一次**（不重复）
      ② **直接抛出**，不静默续写 —— 否则就是内容重复（长度对得上，最难发现）
      ③ 打开一直失败 → 抛错，文件保持空（不假装成功）
    """
    import ctypes as _ct
    import types
    tmpd = tempfile.mkdtemp(prefix='hg-retry-')
    target = os.path.join(tmpd, 'retry.log')
    data = b'ABCDEFGHIJ' * 3
    real_WinDLL = _ct.WinDLL
    out = {'dir': tmpd, 'data_len': len(data)}

    def make_fake(open_fails=0, partial_then_fail=False):
        """造一个假 kernel32。

        ★注意：`_win_append` 会给 `CreateFileW.argtypes` / `.restype` 赋值，
          所以这两个属性必须是**普通函数**（可挂属性），不能是绑定方法
          —— 绑定方法没有 __dict__，赋值会直接 AttributeError。
        """
        st = {'opens': 0, 'writes': 0, 'fh': None,
              'open_fails': open_fails, 'partial': partial_then_fail}

        def CreateFileW(*a):
            st['opens'] += 1
            if st['open_fails'] > 0:
                st['open_fails'] -= 1
                _ct.set_last_error(32)          # ERROR_SHARING_VIOLATION
                return None
            st['fh'] = open(target, 'ab')
            return 0x4242                       # 非 None、非 INVALID

        def WriteFile(h, buf, n, pwritten, ov):
            st['writes'] += 1
            if st['partial'] and st['writes'] == 1:
                half = max(1, n // 2)
                st['fh'].write(_ct.string_at(buf, half))
                st['fh'].flush()
                _ct.set_last_error(112)         # ERROR_DISK_FULL
                return 0                        # FALSE（且 lpNumberOfBytesWritten 无意义）
            st['fh'].write(_ct.string_at(buf, n))
            st['fh'].flush()
            pwritten._obj.value = n
            return 1

        def CloseHandle(h):
            if st['fh'] is not None:
                st['fh'].close()
                st['fh'] = None
            return 1

        ns = types.SimpleNamespace(CreateFileW=CreateFileW, WriteFile=WriteFile,
                                   CloseHandle=CloseHandle)
        return st, ns

    def rd():
        try:
            with open(target, 'rb') as f:
                return f.read()
        except OSError:
            return b''

    def run(ns):
        """用假 kernel32 跑一次 `_win_append`，返回 (状态, 明细)。

        ★入参是 `make_fake()` 返回的 **namespace**（不是 (st, ns) 元组）——
          因为 `_win_append` 内部会访问 `k32.CreateFileW` 等属性，
          这里把 `ctypes.WinDLL` 整个替换掉，让它"以为"自己拿到了 kernel32。
        """
        if os.path.exists(target):
            os.unlink(target)
        _ct.WinDLL = lambda *a, **k: ns
        try:
            n = _win_append(target, data, retries=5, sleep_base=0, sleep_max=0)
            return ('ok', n)
        except Exception as e:                                  # noqa: BLE001
            return ('raise', '%s: %s' % (e.__class__.__name__, e))
        finally:
            _ct.WinDLL = real_WinDLL

    # ① 打开失败 3 次后成功
    st1, ns1 = make_fake(open_fails=3)
    status1, det1 = run(ns1)
    c1 = rd()
    out['retry_then_ok'] = {
        'status': status1, 'detail': det1,
        'opens': st1['opens'], 'writes': st1['writes'],
        'content_ok': c1 == data, 'content_len': len(c1), 'dup': c1.count(data) > 1,
        'ok': status1 == 'ok' and c1 == data and st1['opens'] == 4,
        'expect': '打开失败 3 次后仍成功；内容 == 原文且只出现一次（opens=4）',
    }

    # ② 写一半就失败 —— 必须抛错，绝不能变成"内容重复"
    st2, ns2 = make_fake(partial_then_fail=True)
    status2, det2 = run(ns2)
    c2 = rd()
    out['partial_then_fail'] = {
        'status': status2, 'detail': det2,
        'opens': st2['opens'], 'writes': st2['writes'],
        'content_len': len(c2), 'is_prefix_of_data': data.startswith(c2),
        'ok': (status2 == 'raise' and c2 != data
               and not data.startswith(c2 + data)
               and not c2.endswith(data)),
        'expect': '部分落盘后必须抛出（拒绝续写）；磁盘上只留那半截，不得重复整段',
    }

    # ③ 打开一直失败 —— 抛错且文件为空
    st3, ns3 = make_fake(open_fails=99)
    status3, det3 = run(ns3)
    c3 = rd()
    out['always_fail'] = {
        'status': status3, 'detail': det3,
        'opens': st3['opens'], 'writes': st3['writes'], 'content_len': len(c3),
        'ok': status3 == 'raise' and c3 == b'' and st3['opens'] == 5,
        'expect': '重试 5 次用尽 -> 抛出，文件保持空（不假装成功）',
    }

    out['ok'] = all(out[k]['ok'] for k in ('retry_then_ok', 'partial_then_fail',
                                           'always_fail'))
    return out


def safe_append_bytes(path, data, expect=None, retries=30):
    """向共享文件原子追加一段**字节**。返回结果 dict。**不与别的进程交错、不丢行**。

    ① Windows 走 `_win_append`（FILE_APPEND_DATA，系统级原子追加）；
       失败则降级回 CRT 追加，但**标出来**（`atomic_append=False` + stderr 告警），
       绝不假装成功 —— 降级后是真的可能丢行，调用方必须知道。
    ② POSIX 用 `O_APPEND` + 单次 `os.write`（内核保证原子，无需降级）。
    ③ `expect` 非空时，追加前报告"我上次读到的版本是否已被改过"。
    ④ `retries`：瞬时争用（安全软件/索引器持锁）导致 CreateFileW 偶发失败时的重试次数，
       默认 30（退避）。**这是承载行为，别调成 1** —— 见 `_win_append` docstring。

    ★这是**字节级**入口；`safe_append` 是它的文本版（= `encode` 后调本函数）。
      `wslog_append.atomic_append` 也委托到本函数 —— 保证"原子追加"在仓库里只有一份实现
      （2026-09-17：`wslog_append` 旧版自带一份，且那份在 WriteFile 失败时也重试，
      可能产出**内容重复**；两份实现分叉的代价已经出现过一次，故收归此处）。
    """
    path = os.path.abspath(path)
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    changed = False
    if expect is not None:
        changed = not same_snapshot(expect, snapshot(path))
    if os.name == 'nt':
        try:
            n = _win_append(path, data, retries=retries)
            return {'ok': True, 'path': path, 'bytes': n,
                    'atomic_append': True, 'concurrent_change': changed}
        except Exception as e:
            sys.stderr.write('[hubguard][warn] FILE_APPEND_DATA 追加失败（%s: %s），'
                             '降级为 CRT 追加 —— **该降级下并发追加可能丢行**\n'
                             % (e.__class__.__name__, e))
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags, 0o644)
    try:
        n = os.write(fd, data)
    finally:
        os.close(fd)
    return {'ok': True, 'path': path, 'bytes': n,
            'atomic_append': (os.name != 'nt'), 'concurrent_change': changed}


def safe_append(path, text, expect=None, encoding='utf-8', retries=30):
    """向共享文件追加一段**文本**。**不与别的进程交错、不丢行**。

    实现 = `text.encode(encoding)` 之后交给 `safe_append_bytes`。
    语义、降级规则、`retries` 含义**全在那边的 docstring**（此处不重复，
    否则同一段说明写两处、迟早只改一处）。
    """
    return safe_append_bytes(path, text.encode(encoding),
                             expect=expect, retries=retries)


# ============================================================================
# 投影来源标记（问题②）
# ============================================================================

SRC_CODE = {
    'workbuddy': 'w',        # 国内版
    'workbuddy_ai': 'a',     # 国际版
    'openclaw': 'o',
    'doubao': 'd', 'doubao_a': 'd', 'doubao_b': 'b',
    'user': 'u', 'legacy': 'l', 'reflector': 'r',
    'tool_audit': 't', 'tool_audit.py': 't', 'memtether': 'm',
}

TYP_ABBR = {
    'decision': 'dec', 'incident': 'inc', 'experience': 'exp', 'fact': 'fact',
    'preference': 'pref', 'environment': 'env', 'todo': 'todo',
    'tool': 'tool', 'path': 'path',
}

SRC_LEGEND = '来源码：w=国内版 · a=国际版 · o=OpenClaw · d=豆包 · u=用户 · l=遗留 · r=反思器 · t=资产审计'
TYP_LEGEND = '类型：dec=决策 · inc=事故 · exp=经验 · fact=事实'


def src_code(source):
    s = (source or '').strip()
    if s in SRC_CODE:
        return SRC_CODE[s]
    for k, v in SRC_CODE.items():
        if s.startswith(k):
            return v
    return (s[:1].lower() or '?') if s else '?'


def typ_abbr(typ):
    return TYP_ABBR.get((typ or '').strip(), (typ or '?'))


def format_fact_line(date10, typ, source, lead, abbrev=True, tag_src=True):
    """**投影行的唯一格式真源** —— 生成器与 annotate 都调它，避免两边漂移。

    默认：`- [2026-09-17|exp·a] 首句结论`
    ★为什么把类型缩成 3 字母：投影预算只有 3980 字符（实测余量 93），
      加来源标记要占 2 字符/行；把 dec/inc/exp 缩掉正好**省出 3~5 字符/行**，
      于是「加来源」这个动作的净预算是 **≈0**（实测净省 75 字符）。
      不这么做就得砍掉 1~2 条记忆来给标记腾位置 —— 那是拿信息换格式。
    """
    t = typ_abbr(typ) if abbrev else (typ or '?')
    if tag_src:
        return '- [%s|%s·%s] %s' % (date10, t, src_code(source), lead)
    return '- [%s|%s] %s' % (date10, t, lead)


_LINE_RE = None


def parse_fact_line(line):
    """解析投影事实行 → dict 或 None。兼容带/不带来源标记两种格式。"""
    global _LINE_RE
    if _LINE_RE is None:
        import re
        _LINE_RE = re.compile(r'^- \[(\d{4}-\d{2}-\d{2})\|([^\]·]+)(?:·(.))?\]\s?(.*)$')
    m = _LINE_RE.match(line)
    if not m:
        return None
    return {'date': m.group(1), 'typ': m.group(2), 'src': m.group(3), 'lead': m.group(4)}


def annotate_projection(text, db=None, allow_partial=False, revert=False):
    """给**任意来源**生成的投影补来源标记（幂等、可回退、不静默）。

    归属怎么定：拿行里的正文前缀去库里找 content 以它开头的 active 事实。
    ★匹配不上就**保持原样并计数**，绝不猜 —— 猜错等于把"分不清谁写的"
      升级成"标错了谁写的"，比原来更坏。
    """
    lines = text.split('\n')
    stats = {'total': 0, 'matched': 0, 'unmatched': 0, 'already': 0,
             'reverted': 0, 'skipped_header': 0}
    un_ok = False
    for i, ln in enumerate(lines):
        if ln.startswith('## '):
            un_ok = True                     # 只看「关键事实」区，避免误伤页眉/资产区
            continue
        if not un_ok:
            stats['skipped_header'] += 1
            continue
        p = parse_fact_line(ln)
        if not p:
            continue
        stats['total'] += 1
        if revert:
            if p['src']:
                lines[i] = format_fact_line(p['date'], p['typ'], '', p['lead'],
                                            abbrev=True, tag_src=False)
                stats['reverted'] += 1
            continue
        if p['src']:
            stats['already'] += 1
            continue
        src = _lookup_source(p['lead'], db)
        if not src:
            stats['unmatched'] += 1
            continue
        lines[i] = format_fact_line(p['date'], p['typ'], src, p['lead'],
                                    abbrev=True, tag_src=True)
        stats['matched'] += 1
    if stats['unmatched'] and not allow_partial:
        raise RuntimeError('有 %d 条无法归属（不猜、不改）：要么调 --allow-partial，'
                           '要么先让生成器直接带 source。' % stats['unmatched'])
    return '\n'.join(lines), stats


def _lookup_source(lead, db=None):
    """按正文前缀反查 source。返回 None 表示查不到（调用方必须处理）。"""
    core = (lead or '').rstrip('…').strip()
    if len(core) < 8:
        return None
    try:
        conn = sqlite3.connect(db_path(db), timeout=3)
        conn.row_factory = sqlite3.Row
        for key in (core[:60], core[:40], core[:24]):
            r = conn.execute(
                "SELECT source, content FROM facts WHERE status='active' "
                "AND substr(content,1,?)=? ORDER BY COALESCE(NULLIF(updated_at,''),created_at) DESC",
                (len(key), key)).fetchall()
            if len(r) == 1:
                conn.close()
                return r[0]['source']
            if len(r) > 1:
                srcs = {x['source'] for x in r}
                conn.close()
                return srcs.pop() if len(srcs) == 1 else None
        conn.close()
    except Exception:
        return None
    return None


# ============================================================================
# install_guards —— 不改函数体，接管任意版本的 gateway.py
# ============================================================================

_GUARD_NAMES = ('remember', 'correct', 'retire', 'record_tool', 'record_incident',
                'incident', 'commit_memory_candidate', 'process_events', 'rebuild',
                'on_miss', 'auto_reflect')


def _wrap(func, name, timeout):
    def wrapper(*a, **k):
        with hub_lock(timeout=timeout, purpose='gateway.%s' % name):
            return func(*a, **k)
    wrapper._hub_guarded = True
    wrapper.__name__ = getattr(func, '__name__', name)
    wrapper.__doc__ = getattr(func, '__doc__', None)
    return wrapper


def install_guards(ns, names=_GUARD_NAMES, timeout=None):
    """把 `ns`（通常是 `globals()`）里的写入型函数逐个套上锁。**幂等**。

    ★为什么用包装而不是改函数体：本机存在**两个不同版本**的 gateway.py
      （memory_hub 72550 B 在制品 / memtether 60632 B 发布版），
      逐行打补丁必然对不齐；包装器只依赖"函数名存在"，对两版**都适用**。
    """
    done = []
    for n in names:
        f = ns.get(n)
        if not callable(f) or getattr(f, '_hub_guarded', False):
            continue
        ns[n] = _wrap(f, n, timeout)
        done.append(n)
    return done


# ============================================================================
# journal —— 中枢变更日志（问题①的"能注意到"）
# ============================================================================

def journal_record(db=None, note=''):
    """把当前库指纹追加进日志；若与上一条不同，顺带算出**是谁写的**。"""
    fp = db_fingerprint(db)
    jp = journal_path(db)
    prev = None
    try:
        # 只读尾部若干 KB 找最后一条 —— journal 是只追加的，全量读会随历史线性变慢
        sz = os.path.getsize(jp)
        with open(jp, 'rb') as f:
            f.seek(max(0, sz - 8192))
            chunk = f.read().decode('utf-8', 'replace')
        for ln in chunk.splitlines():
            ln = ln.strip()
            if ln.startswith('{'):
                try:
                    prev = json.loads(ln)
                except Exception:
                    pass
    except Exception:
        pass
    changed = (prev is None) or (prev.get('fp') != fp.get('fp'))
    entry = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'fp': fp.get('fp'),
             'size': fp.get('size'), 'facts_n': (fp.get('facts') or {}).get('n'),
             'facts_max_id': (fp.get('facts') or {}).get('max_id'),
             'audit_max_id': (fp.get('audit_log') or {}).get('max_id'),
             'changed': changed, 'note': note}
    if changed and prev is not None:
        entry['delta_facts'] = (entry['facts_n'] or 0) - (prev.get('facts_n') or 0)
        entry['writers'] = _writers_since(db, prev.get('facts_max_id') or 0)
    elif changed:
        entry['delta_facts'] = entry['facts_n']
        entry['writers'] = _writers_since(db, 0)
    try:
        # 用 O_APPEND 单次写：journal 可能被两侧同时追加，普通 open('a') 的文本层
        # 会分多次 write，行与行之间有被交错撕裂的窗口
        safe_append(jp, json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        entry['write_error'] = str(e)
    return entry


def _writers_since(db, since_id):
    """id > since_id 的新事实，按 source 汇总 —— 直接回答"是谁写的"。"""
    try:
        conn = sqlite3.connect(db_path(db), timeout=3)
        conn.row_factory = sqlite3.Row
        r = conn.execute('SELECT source, COUNT(*) n, MIN(created_at) a, MAX(created_at) b '
                         'FROM facts WHERE id>? GROUP BY source ORDER BY b', (int(since_id),)).fetchall()
        conn.close()
        return [{'source': x['source'], 'n': x['n'], 'from': x['a'], 'to': x['b']} for x in r]
    except Exception as e:
        return [{'error': str(e)}]


# ============================================================================
# 体检
# ============================================================================

def _read_text(p):
    try:
        with open(p, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return None


def status(db=None, as_json=False):
    fp = db_fingerprint(db)
    ls = lock_status(db)
    projs = []
    for p in proj_paths():
        t = _read_text(p)
        projs.append({'path': p, 'exists': t is not None,
                      'chars': len(t) if t is not None else None,
                      'budget': proj_budget(),
                      'headroom': (proj_budget() - len(t)) if t is not None else None,
                      'mtime': time.strftime('%H:%M:%S', time.localtime(os.stat(p).st_mtime))
                      if os.path.exists(p) else None,
                      'islink': os.path.islink(p)})
    out = {'now': time.strftime('%Y-%m-%d %H:%M:%S'), 'db': fp, 'lock': ls, 'projections': projs}
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return out
    print('时间        %s' % out['now'])
    print('库          %s' % fp.get('path'))
    if not fp.get('exists'):
        print('            ✘ 库不存在（解析方式 %s）—— 用 --db 或 MEM_DB 指定真源库'
              % fp.get('origin'))
    else:
        print('            %s B · %s · facts %s 条(max_id=%s) · journal_mode=%s · 解析=%s'
              % (fp.get('size'), fp.get('mtime'),
                 (fp.get('facts') or {}).get('n'), (fp.get('facts') or {}).get('max_id'),
                 fp.get('journal_mode'), fp.get('origin')))
        if str(fp.get('origin', '')).startswith('discovered'):
            print('            ⚠ 未按 HUB/memory.db 命中，而是**发现**到的库 —— '
                  '确认它就是要守的那个（守错库 = 锁在 A、写落在 B）')
    print('指纹        %s' % fp.get('fp'))
    print('锁          %s' % ls['path'])
    if ls['held']:
        h = ls['holder']
        print('            ✘ 正被占用：pid=%s agent=%s 自 %s 用途=%s'
              % (h.get('pid'), h.get('agent'), h.get('since'), h.get('purpose') or '-'))
    else:
        print('            ✔ 空闲' + ('（残留 holder 记录，持有进程已退出）'
                                     if ls['stale_holder'] else ''))
    for p in projs:
        print('投影        %s' % p['path'])
        print('            %s 字符 / 预算 %s / 余量 %s · mtime=%s%s'
              % (p['chars'], p['budget'], p['headroom'], p['mtime'],
                 ' · 符号链接' if p['islink'] else ''))
    print('来源分布')
    for s, mx in (fp.get('sources') or []):
        print('            %-16s 最后写入 %s' % (s, mx))
    return out


def doctor(db=None, as_json=False):
    """全面体检。每条检查都给「结论 + 依据 + 下一步」，不做没有依据的断言。"""
    checks = []

    def add(level, name, detail, fix=''):
        checks.append({'level': level, 'name': name, 'detail': detail, 'fix': fix})

    # 1) 锁
    ls = lock_status(db)
    if ls['held']:
        h = ls['holder']
        add('info', '中枢写锁', '正被 pid=%s（agent=%s）占用，自 %s' %
            (h.get('pid'), h.get('agent'), h.get('since')))
    elif ls['stale_holder']:
        add('warn', '中枢写锁', '空闲，但残留 holder 记录（持有进程已退出）',
            '下一次取锁会自动覆盖该记录；也可删除 %s' % holder_path(ls['path']))
    else:
        add('ok', '中枢写锁', '空闲')

    # 2) 投影槽位 + 落后
    fp = db_fingerprint(db)
    for p in proj_paths():
        t = _read_text(p)
        if t is None:
            add('warn', '投影可读', '读不到：%s' % p, '检查路径/权限')
            continue
        b = proj_budget()
        if len(t) > b:
            add('fail', '投影槽位', '%s：%d 字符 > 预算 %d —— 注入时会被**整体截断**'
                % (os.path.basename(p), len(t), b), '减少写入量或收紧 MEM_PROJ_BUDGET 相关裁剪')
        elif len(t) > b - 100:
            add('warn', '投影槽位', '%s：%d 字符，余量仅 %d（预算 %d）'
                % (os.path.basename(p), len(t), b - len(t), b), '再写几条就会触发裁剪')
        else:
            add('ok', '投影槽位', '%s：%d 字符，余量 %d' % (os.path.basename(p), len(t), b - len(t)))
        try:
            dm, pm = os.stat(db_path(db)).st_mtime, os.stat(p).st_mtime
            if pm + 1 < dm:
                add('warn', '投影新鲜度', '投影比库旧 %.0f 秒（库 %s / 投影 %s）—— '
                    '说明最后一次写入之后没人 rebuild' %
                    (dm - pm, time.strftime('%H:%M:%S', time.localtime(dm)),
                     time.strftime('%H:%M:%S', time.localtime(pm))),
                    '在锁保护下跑一次 rebuild')
            else:
                add('ok', '投影新鲜度', '投影不落后于库')
        except Exception as e:
            add('warn', '投影新鲜度', str(e))

    # 3) 来源可分辨
    t = _read_text(proj_paths()[0])
    if t is not None:
        n_line = sum(1 for ln in t.split('\n') if parse_fact_line(ln))
        n_src = sum(1 for ln in t.split('\n')
                    if (parse_fact_line(ln) or {}).get('src'))
        if n_line and n_src == 0:
            add('fail', '投影来源可分辨', '%d 条事实**无一带来源标记** → 看投影分不清谁写的'
                % n_line, 'python hubguard.py annotate   （或让生成器直接带 source）')
        elif n_line and n_src < n_line:
            add('warn', '投影来源可分辨', '%d/%d 条带来源标记' % (n_src, n_line))
        else:
            add('ok', '投影来源可分辨', '%d/%d 条带来源标记' % (n_src, n_line))

    # 4) 共享 inode（跨客户端互相覆盖的物理前提）
    #    ★按 (dev, ino) 归组，而不是两两配对：两侧共 8+8 个工作区全部软链到同一份时，
    #      两两配对会算出 120 对、打印一屏噪声，而真正的结论只有一句"它们全是同一个文件"。
    groups = {}
    cands = [os.path.expanduser(os.path.join('~', '.workbuddy', 'MEMORY.md')),
             os.path.expanduser(os.path.join('~', '.workbuddy-ai', 'MEMORY.md'))]
    cands += workspace_memory_paths()
    for p in cands:
        try:
            st = os.stat(p)
        except Exception:
            continue
        groups.setdefault((st.st_dev, st.st_ino), []).append(p)
    shared = [(k, v) for k, v in groups.items() if len(v) > 1]
    if shared:
        tot = sum(len(v) for _, v in shared)
        detail = []
        for k, v in sorted(shared, key=lambda x: -len(x[1]))[:3]:
            ws = [x for x in v if os.sep + 'Work' in x]
            detail.append('%d 条路径同一 inode（其中工作区 %d 条）' % (len(v), len(ws)))
        add('warn', '跨客户端共享文件',
            '实测 %d 组、共 %d 条路径指向**同一物理文件**：%s' % (len(shared), tot, ' ; '.join(detail)),
            '重写这类文件前必须先 snapshot() 再 commit_guarded()，否则会静默覆盖对方改动')
        add('info', '共享文件样例', ' ↔ '.join(shared[0][1][:3]))
    else:
        add('ok', '跨客户端共享文件', '未发现同 inode 的路径对')

    # 5) 侧车文件 / journal_mode
    for suffix in ('-wal', '-shm', '-journal'):
        sp = db_path(db) + suffix
        if os.path.exists(sp):
            jm = fp.get('journal_mode')
            if suffix in ('-wal', '-shm') and jm != 'wal':
                add('warn', 'SQLite 侧车文件', '存在 %s 但 journal_mode=%s（不一致）' % (suffix, jm),
                    '可能残留自 WAL 会话；确认无进程在用后可删')
            else:
                add('info', 'SQLite 侧车文件', '存在 %s（journal_mode=%s）' % (suffix, jm))
    if fp.get('journal_mode') != 'wal':
        add('info', 'journal_mode', '%s（默认，未启用 WAL）' % fp.get('journal_mode'),
            '本模块**刻意不自动改**：WAL 是库级持久设置，会让 `cp memory.db` 丢掉 -wal 里的提交。'
            '确需开启用 MEM_DB_JOURNAL=wal，并同步改所有备份脚本')

    # 6) 谁在写
    srcs = fp.get('sources') or []
    if srcs:
        add('info', '来源分布', ' · '.join('%s→%s' % (s, m) for s, m in srcs[:4]))

    verdict = 'fail' if any(c['level'] == 'fail' for c in checks) \
        else ('warn' if any(c['level'] == 'warn' for c in checks) else 'ok')
    out = {'verdict': verdict, 'checks': checks}
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return out
    icon = {'ok': '✔', 'warn': '⚠', 'fail': '✘', 'info': '·'}
    print('hubguard doctor —— 结论：%s' % {'ok': '全部通过', 'warn': '有提醒',
                                            'fail': '有必须修的问题'}[verdict])
    print('-' * 78)
    for c in checks:
        print('%s %-16s %s' % (icon[c['level']], c['name'], c['detail']))
        if c['fix']:
            print('  %-16s → %s' % ('', c['fix']))
    return out


# ============================================================================
# selftest —— 证明锁真的独占
# ============================================================================

def _hold_child(seconds, use_lock, db=None, timeout=5.0, wrapped=False):
    t0 = time.time()
    rec = {'t0': t0, 'pid': os.getpid(), 'use_lock': use_lock, 'wrapped': wrapped,
           'db': db_path(db)}
    try:
        if wrapped:
            # ★走**包装后**的写入函数，而不是直接拿锁 —— 证明 install_guards 真的接管了。
            #   必须起子进程才测得出来：同进程内锁是**可重入**的（见 D2），
            #   父进程持锁时子调用会顺利重入，测不出任何东西。
            os.environ['MEM_LOCK_TIMEOUT'] = str(timeout)
            ns = {'remember': lambda *a, **k: 'wrote'}
            install_guards(ns)
            rec['guarded'] = getattr(ns['remember'], '_hub_guarded', False)
            rec['t_acquired'] = time.time()
            ns['remember']('x')
            time.sleep(seconds)
        elif use_lock:
            # ★必须把 db 透传下去：否则父进程锁 A 库、子进程锁 HUB 库 → C 组会假通过
            with hub_lock(timeout=timeout, agent='selftest-child', purpose='hold', db=db):
                rec['t_acquired'] = time.time()
                time.sleep(seconds)
        else:
            rec['t_acquired'] = time.time()
            time.sleep(seconds)
        rec['ok'] = True
    except HubBusy as e:
        rec['ok'] = False
        rec['busy'] = str(e)
    rec['t_end'] = time.time()
    print(json.dumps(rec, ensure_ascii=False))


def _append_child(n, path, tag):
    """并发追加演练用：向共享文件追加 n 行定长记录。"""
    last = {}
    for i in range(n):
        last = safe_append(path, '%s-%04d-%s\n' % (tag, i, 'x' * 40))
    print(json.dumps({'ok': True, 'pid': os.getpid(), 'tag': tag, 'n': n,
                      'atomic_append': last.get('atomic_append')}))


def selftest(db=None):
    """★验收口径不是"跑通了"，而是**故意制造并发，看它报不报**。

    A 对照组（不用锁）：两个子进程各持 1.2s → 应当**并行**完成（≈1.2s）
      ⇒ 证明"不用锁时确实会重叠"，即这套演练有鉴别力。
    B 实验组（用锁）  ：同样两个子进程 → 应当**串行**（≈2.4s），
      且**后者**的 t_acquired - t0 ≥ 0.7s ⇒ 证明锁真的互斥了。
    C 超时组          ：父进程持锁时，子进程 timeout=0.6 必须**失败**并报出持有者 pid。
    D 包装组          ：install_guards 必须真的接管写入函数，且**幂等**。
    E 追加组          ：两进程各 safe_append 200 行 → 必须 400 行全在、无交错撕裂。
    G 隔离组          ：自检本身**不得碰到线上库**（见下）。

    ★★隔离铁律（2026-09-17 实测踩到后补）：不传 `--db` 时**绝不走发现规则**。
      在发布仓 `memtether/` 里跑 `hubguard.py selftest`，发现链的 'sibling' 规则
      会命中「线上真源库」（记忆中枢仓库里的 memory.db）→ 自检在**对方的目录**
      里建了 `memory.db.hub.lock`，还短暂持有了线上锁 —— 对方此刻若在写就会被挡住。
      自检只该验证"机制会不会报错"，不该有跨客户端副作用。
      → 现在默认落在临时目录；**并且把 `MEM_DB` 也钉住** —— 只钉局部变量 `db` 挡不住
        `install_guards` 包装后的函数（它内部走 `db_path(None)`），D2 会照样去锁线上库
        （既假绿、又真干扰）。
      → 确需对线上库跑自检：显式 `--db <库>` + `MEM_SELFTEST_ALLOW_LIVE=1`。
    """
    isolated = db is None
    if isolated:
        db = os.path.join(tempfile.mkdtemp(prefix='hg-selftest-'), 'memory.db')
    _env_before = os.environ.get('MEM_DB')
    os.environ['MEM_DB'] = db
    _DB_CACHE.clear()
    resolved_db = os.path.abspath(db_path(db))
    # 发现规则会挑中的"线上库"（sibling：`<HUB 的父目录>/memory_hub/memory.db`）
    live_guess = os.path.abspath(os.path.join(os.path.dirname(HUB), 'memory_hub', 'memory.db'))
    touches_live = (resolved_db == live_guess)
    if touches_live:
        sys.stderr.write('[selftest][warn] ★本次自检会锁线上库：%s\n' % resolved_db)
        sys.stderr.write('[selftest][warn]   对方客户端若正在写会被挡住。'
                         '加 MEM_SELFTEST_ALLOW_LIVE=1 表示知情。\n')
    py = sys.executable
    me = os.path.abspath(__file__)
    results = {}
    HOLD = 1.2

    def _flags():
        return (['--db', db] if db else [])

    def spawn(n, use_lock, timeout=6.0, extra=None):
        ps = []
        for _ in range(n):
            cmd = [py, me] + _flags() + ['_hold', str(HOLD),
                                         '--lock' if use_lock else '--no-lock',
                                         '--timeout', str(timeout)]
            ps.append(subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        return ps

    def collect(ps):
        out = []
        for p in ps:
            so, se = p.communicate(timeout=60)
            for ln in (so or '').splitlines():
                ln = ln.strip()
                if ln.startswith('{'):
                    try:
                        out.append(json.loads(ln))
                    except Exception:
                        pass
        return out

    t = time.time()
    recs = collect(spawn(2, False))
    results['A_no_lock'] = {'wall': round(time.time() - t, 2),
                            'ok_count': sum(1 for r in recs if r.get('ok')),
                            'expect': 'wall≈%.1fs（并行重叠）' % HOLD}
    t = time.time()
    recs = collect(spawn(2, True))
    waits = sorted(round(r.get('t_acquired', 0) - r.get('t0', 0), 2) for r in recs)
    results['B_with_lock'] = {'wall': round(time.time() - t, 2), 'waits': waits,
                              'ok_count': sum(1 for r in recs if r.get('ok')),
                              'expect': 'wall≈%.1fs（串行），后者等待≥0.7s' % (HOLD * 2)}

    with hub_lock(agent='selftest-parent', purpose='probe', db=db):
        p = subprocess.Popen([py, me] + _flags() + ['_hold', '0.1', '--lock', '--timeout', '0.6'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding='utf-8', errors='replace',
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        so, se = p.communicate(timeout=60)
        rec = {}
        for ln in (so or '').splitlines():
            if ln.strip().startswith('{'):
                rec = json.loads(ln.strip())
        results['C_busy'] = {'child_ok': rec.get('ok'),
                             'busy_msg': (rec.get('busy') or '')[:140],
                             'expect': 'child_ok=False，且报出持有者 pid='}

    # D：包装器接管 + 幂等 + 可重入 + 跨进程真的会被挡
    ns = {'remember': lambda *a, **k: 'wrote', 'rebuild': lambda *a, **k: 'rebuilt'}
    first = install_guards(ns)
    second = install_guards(ns)
    d_ok = (set(first) == {'remember', 'rebuild'}
            and getattr(ns['remember'], '_hub_guarded', False)
            and getattr(ns['rebuild'], '_hub_guarded', False)
            and second == [])
    # D2 同进程可重入：自己持锁时调自己包装过的函数**不应**报错（这是刻意的设计，
    #    否则 rebuild 内部再调 remember 就会自锁死）
    d_reentrant = None
    try:
        with hub_lock(agent='selftest-parent', purpose='probe-d2', db=db):
            ns['remember']()
            d_reentrant = True
    except HubBusy:
        d_reentrant = False
    # D3 跨进程必被挡：父进程持锁时，子进程跑包装后的函数必须抛 HubBusy 并报出 pid
    d_blocked = None
    d_child = {}
    d_same_db = None
    try:
        with hub_lock(agent='selftest-parent', purpose='probe-d3', db=db):
            p = subprocess.Popen([py, me] + _flags() + ['_hold', '0.1', '--wrapped',
                                                       '--timeout', '0.6'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding='utf-8', errors='replace',
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            so, se = p.communicate(timeout=60)
            for ln in (so or '').splitlines():
                if ln.strip().startswith('{'):
                    d_child = json.loads(ln.strip())
            # ★先验库一致性：父子若锁的不是同一个库，D3 就是**假通过/假失败**，
            #   必须先把这条报出来，否则会被误读成"锁没生效"。
            _cdb = d_child.get('db') or ''
            d_same_db = bool(_cdb) and os.path.abspath(_cdb) == os.path.abspath(db_path(db))
            d_blocked = (d_same_db
                         and d_child.get('ok') is False
                         and d_child.get('guarded') is True
                         and 'pid=' in (d_child.get('busy') or ''))
    except Exception as e:
        d_blocked = 'err:%s' % e
    results['D_wrap'] = {'installed': first, 'idempotent_second_call': second,
                         'reentrant_same_process': d_reentrant,
                         'child_blocked': d_blocked,
                         'child_db': d_child.get('db'), 'parent_db': db_path(db),
                         'same_db': d_same_db,
                         'child_busy': (d_child.get('busy') or '')[:140],
                         'expect': "installed=['remember','rebuild']，二次=[]，"
                                   "同进程可重入=True，跨进程被挡=True（且父子锁同一库）"}

    # E：并发追加（问题③的机制）
    tmpd = tempfile.mkdtemp(prefix='hg-append-')
    sh = os.path.join(tmpd, 'shared.txt')
    N = 200
    ps = [subprocess.Popen([py, me, '_append', str(N), sh, tag],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           encoding='utf-8', errors='replace',
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
          for tag in ('p1', 'p2')]
    arecs = collect(ps)
    e_atomic = bool(arecs) and all(r.get('atomic_append') for r in arecs)
    try:
        with open(sh, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []
    intact = sum(1 for ln in lines
                 if ln.count('-') == 2 and len(ln.split('-')[1]) == 4
                 and ln.split('-')[1].isdigit() and ln.split('-')[2] == 'x' * 40)
    results['E_append'] = {'lines': len(lines), 'intact': intact, 'expect_n': N * 2,
                           'atomic_append': e_atomic, 'file': sh,
                           'expect': '%d 行全在且每行完整（Windows 走 FILE_APPEND_DATA；'
                                     'CRT 的 O_APPEND 实测会丢 1~5 行）' % (N * 2)}

    # F：符号链接保护 —— 双版本互通（~/.workbuddy-ai/MEMORY.md -> ~/.workbuddy/MEMORY.md）
    #    全靠符号链接。os.replace 直写会把**链接本身**换成普通文件，
    #    两个客户端从此各写各的、互通被静默打断。本组必须证明链接还在。
    tmpf = tempfile.mkdtemp(prefix='hg-symlink-')
    real = os.path.join(tmpf, 'real.txt')
    link = os.path.join(tmpf, 'link.txt')
    f_info = {'real': real, 'link': link}
    try:
        with open(real, 'w', encoding='utf-8', newline='') as f:
            f.write('AAA\n')
        os.symlink(real, link)
        r = atomic_write(link, 'BBB\n')
        with open(real, encoding='utf-8') as f:
            real_now = f.read().strip()
        with open(link, encoding='utf-8') as f:
            link_now = f.read().strip()
        f_info.update({
            'still_symlink': os.path.islink(link),
            'real_content': real_now, 'link_content': link_now,
            'writes_real': r.get('real'), 'symlink_flag': r.get('symlink'),
            'ok': (os.path.islink(link) and real_now == 'BBB'
                   and link_now == 'BBB' and r.get('symlink') is True),
            'expect': '写入后链接仍是链接、真身内容已更新'
                      '（若走 os.replace 直写，链接会被换成普通文件）',
        })
    except OSError as e:
        f_info.update({'skipped': True, 'ok': True, 'why': str(e),
                       'expect': '本机无法创建符号链接（需 SeCreateSymbolicLinkPrivilege），本组跳过'})
    results['F_symlink'] = f_info

    # H：追加重试语义（确定性注入故障，不靠碰运气）
    #    ★为什么要单列一组：重试是"承载行为"（丢的是一整个进程的一整帧），
    #      而"重试写错方向"会变成内容重复 —— 比丢行更难发现。两种错都必须被钉死。
    h_info = _probe_append_retry()
    results['H_append_retry'] = h_info

    A, B, C, D, E = (results['A_no_lock'], results['B_with_lock'], results['C_busy'],
                     results['D_wrap'], results['E_append'])
    allow_live = os.environ.get('MEM_SELFTEST_ALLOW_LIVE') == '1'
    g_isolated = (not touches_live) or allow_live
    verdict = {
        # ★2026-09-19 修（判据假红）：原判据 `A['wall'] < HOLD + 0.7` 隐含假设
        #   「子进程启动开销 < 0.7s」。本机实测裸解释器冷启动 **0.748s**（min 0.702，
        #   见 _probe_spawn_cost.py）⇒ 理论 wall = 0.748 + 1.2 = **1.95s 恒 > 1.90s**
        #   ⇒ A 组**结构性假红**，与锁机制无关（ok_count==2 已证明两个子进程确实重叠）。
        #   改为**相对判据**：只验「不加锁的 wall 明显短于加锁的 wall」，不依赖机器速度。
        #   ★强度未降：若锁失效致 A 退化为串行，A.wall → B.wall，差值判据必红。
        'A_并行重叠': (A['ok_count'] == 2
                       and A['wall'] < B['wall'] - HOLD * 0.5
                       and A['wall'] < HOLD * 3),
        'B_被串行化': (B['wall'] > HOLD * 2 * 0.85 and B['ok_count'] == 2
                       and B['waits'] and B['waits'][-1] >= 0.7
                       and B['wall'] - A['wall'] >= HOLD * 0.6),
        'C_占锁时报错': C['child_ok'] is False and 'pid=' in C['busy_msg'],
        'D_包装接管且幂等': bool(d_ok) and d_reentrant is True and d_blocked is True,
        'E_追加不撕裂': E['lines'] == E['expect_n'] and E['intact'] == E['expect_n'],
        'F_符号链接不被替换': bool(f_info.get('ok')),
        'G_自检不碰线上库': bool(g_isolated),
        'H_追加重试不重发': bool(h_info.get('ok')),
    }
    env = {'db': resolved_db, 'isolated': isolated, 'live_guess': live_guess,
           'touches_live_db': touches_live, 'allow_live_override': allow_live,
           'mem_db': os.environ.get('MEM_DB')}
    # 还原环境（子进程都已收集完毕）
    if _env_before is None:
        os.environ.pop('MEM_DB', None)
    else:
        os.environ['MEM_DB'] = _env_before
    _DB_CACHE.clear()
    ok = all(verdict.values())
    if ok:
        # 全绿才清理；有失败就留着现场供复查
        try:
            import shutil
            shutil.rmtree(os.path.dirname(E['file']), ignore_errors=True)
            E['cleaned'] = True
            shutil.rmtree(tmpf, ignore_errors=True)
            f_info['cleaned'] = True
            shutil.rmtree(h_info['dir'], ignore_errors=True)
            h_info['cleaned'] = True
            if isolated:
                shutil.rmtree(os.path.dirname(db), ignore_errors=True)
                env['cleaned'] = True
        except Exception:
            E['cleaned'] = False
            f_info['cleaned'] = False
            h_info['cleaned'] = False
            env['cleaned'] = False
    print(json.dumps({'ok': ok, 'env': env, 'detail': results, 'verdict': verdict},
                     ensure_ascii=False, indent=2))
    return ok


def _alias_kind(path, real):
    """这个路径是**怎么**指向别人的？返回 `(kind, note)`。

    ★为什么必须分清楚（2026-09-17 本机实测，两种机制**同时存在**）：
      · `symlink` —— 文件级符号链接，`os.path.islink(path)` 为 True。
        用户级槽位就是这种：`~/.workbuddy-ai/MEMORY.md` → `~/.workbuddy/MEMORY.md`。
        `os.replace` 会把**链接本身**换成普通文件 → `atomic_write` 必须先 realpath。
      · `junction` —— **目录级**联接：文件自己 `islink=False`、`reparse_tag=0`、
        `st_file_attributes=32`（纯 ARCHIVE），**但父目录是 junction**，
        所以 `realpath(path) != abspath(path)`。本机 15 条工作区路径全是这种。
        ★**光看 `islink` 会把它判成"普通文件、无风险"** —— 而它其实是共享文件，
          正是"看起来没问题、实际会互相覆盖"的典型。必须靠 `abspath != realpath` 才识破。
      · `hardlink` —— `st_nlink > 1`。`os.replace` 会让别的链接**静默指向旧 inode**。
      · `plain` —— 独立文件，与别人无关。

    ★`real` 由调用方传进来（已算过 realpath，避免重复解析）。
    """
    if os.path.islink(path):
        return 'symlink', '文件级符号链接：os.replace 会把链接换成普通文件，必须先 realpath'
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(real):
        return 'junction', ('★共享文件，但 islink=False（父目录是 junction）—— '
                            '判据是 realpath≠abspath；重写前必须 snapshot+commit_guarded')
    try:
        n = os.stat(path).st_nlink
    except Exception:
        n = 1
    if n > 1:
        return 'hardlink', '硬链接 nlink=%d：os.replace 会让别的链接指向旧 inode' % n
    return 'plain', ''


def shared_report(as_json=False):
    """**只读**体检：把"跨客户端共享的注入槽位"逐个列清楚，供动手前核对。

    ★为什么单独做一条命令（2026-09-17）：`doctor` 里已有"共享 inode"的**汇总**判定
      （只报"实测 N 组路径指向同一物理文件"）。但真要动手改这些文件之前，需要的是
      **逐文件明细**，少任何一项都可能写出静默失败：
        · `st_ino / st_dev` —— 两个路径是不是同一份物理文件（互通是否真的成立）；
        · `nlink`         —— 有硬链接时 `os.replace` 会让别的链接**静默指向旧 inode**
                              （`atomic_write` 会据此降级为就地写）；
        · `islink`        —— 是符号链接时 `os.replace` 会把**链接本身**换成普通文件，
                              双版本互通被静默打断（必须先 realpath）；
        · `newline`       —— 写回时用 CRLF 还是 LF。写错则"内容没变、字节全变"；
        · `chars / 预算`  —— 越线是**整篇截断**且静默，必须动手前就知道余量。

    ★**本命令严格只读**：不建目录、不写快照、不碰锁、不改任何文件、不碰数据库。
      退出码：0 = 全部路径可读**且都在预算内**；1 = 有路径读不到，**或有文件已超预算**
      （**fail-closed**：读不到不假装"文件不存在所以没问题"；超预算不假装"只是大了点"
      —— 超预算是**整篇截断**，属于同一类静默失败）。口径与 `doctor`（verdict=fail → 1）一致。

    ★别名判据见 `_alias_kind`：**光看 `islink` 会漏掉目录级 junction**
      （本机 15 条工作区路径全是这种：`islink=False` 但共享同一 inode）。
    """
    cands = [os.path.expanduser(os.path.join('~', '.workbuddy', 'MEMORY.md')),
             os.path.expanduser(os.path.join('~', '.workbuddy-ai', 'MEMORY.md'))]
    cands += workspace_memory_paths()

    items, seen, bad = [], set(), 0
    for p in cands:
        # ★去重只能用**字面路径**，绝不能用 realpath：
        #   本命令存在的意义就是报"哪几条路径其实是同一份文件"。用 realpath 去重会把
        #   15 个别名塌缩成 1 条，再得出与事实**相反**的结论 —— 初版实测就是这样报出
        #   "未发现同 inode 的路径对"，而真相是 15 条路径共享同一 inode。
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        snap = snapshot(p)
        rec = {'path': p, 'exists': bool(snap.get('exists'))}
        if not rec['exists']:
            bad += 1
            rec['error'] = snap.get('error') or '文件不存在'
            items.append(rec)
            continue
        real = os.path.realpath(p)
        rec['alias_kind'], rec['alias_note'] = _alias_kind(p, real)
        rec['islink'] = os.path.islink(p)
        rec['realpath'] = real if rec['alias_kind'] != 'plain' else None
        rec['size'] = snap.get('size')
        rec['chars'] = snap.get('chars')
        rec['nlink'] = snap.get('nlink')
        rec['st_ino'] = snap.get('st_ino')
        rec['st_dev'] = snap.get('st_dev')
        rec['sha256_12'] = (snap.get('sha256') or '')[:12]
        rec['newline'] = 'CRLF' if detect_newline(p) == '\r\n' else 'LF'
        rec['budget'] = slot_budget(p)
        rec['headroom'] = rec['budget'] - (rec['chars'] or 0)
        items.append(rec)

    # 按 (dev, ino) 归组 —— 这是"是不是同一份物理文件"的**唯一**判据。
    groups = {}
    for rec in items:
        if rec.get('exists'):
            groups.setdefault((rec['st_dev'], rec['st_ino']), []).append(rec['path'])
    shared = [{'dev': k[0], 'ino': k[1], 'n': len(v), 'paths': v}
              for k, v in groups.items() if len(v) > 1]
    shared.sort(key=lambda g: -g['n'])

    # 风险点（写回时会不会触发降级 / 会不会被静默截断）—— 归组之后再算，便于后续扩展
    for rec in items:
        if not rec.get('exists'):
            continue
        risk = []
        if rec['alias_kind'] != 'plain':
            risk.append(rec['alias_note'])
        if rec['headroom'] < 0:
            risk.append('已超预算 %d 字符→注入时会被整篇截断' % -rec['headroom'])
        elif rec['headroom'] < 100:
            risk.append('余量仅 %d→再写几条即触发截断' % rec['headroom'])
        rec['risk'] = risk

    over = [r['path'] for r in items if r.get('exists') and r['headroom'] < 0]
    out = {'ok': bad == 0 and not over, 'count': len(items), 'unreadable': bad,
           'over_budget': over, 'shared_groups': shared, 'files': items,
           'note': '只读命令：不建目录、不写快照、不碰锁、不改文件'}

    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out['ok'] else 1

    print('共享槽位体检（只读）—— %d 条路径，%d 条读不到' % (len(items), bad))
    print('-' * 78)
    for rec in items:
        if not rec['exists']:
            print('  [MISS] %s  （%s）' % (rec['path'], rec['error']))
            continue
        tag = {'symlink': 'LINK', 'junction': 'JUNC',
               'hardlink': 'HARD', 'plain': 'FILE'}[rec['alias_kind']]
        print('  [%s] %s' % (tag, rec['path']))
        print('        ino=%s nlink=%s size=%s chars=%s/%s 余 %s nl=%s sha=%s'
              % (rec['st_ino'], rec['nlink'], rec['size'],
                 rec['chars'], rec['budget'], rec['headroom'], rec['newline'],
                 rec['sha256_12']))
        if rec['realpath']:
            print('        -> %s' % rec['realpath'])
        for r in rec['risk']:
            print('        ! %s' % r)
    print('-' * 78)
    if shared:
        print('同一物理文件（%d 组，共 %d 条路径 / 全量 %d 条）：'
              % (len(shared), sum(g['n'] for g in shared), len(items)))
        for g in shared:
            print('  ino=%s × %d 条：' % (g['ino'], g['n']))
            for x in g['paths']:
                print('      %s' % x)
    else:
        print('未发现同 inode 的路径对 —— 跨客户端互通**当前不成立**（别急着改文件）')
    if over:
        print('★超预算 %d 个（注入时会被**整篇截断**）：' % len(over))
        for r in items:
            if r.get('exists') and r['headroom'] < 0:
                print('  %s  %d/%d' % (r['path'], r['chars'], r['budget']))
    return 0 if out['ok'] else 1


# ============================================================================
# CLI
# ============================================================================

def main():
    ap = argparse.ArgumentParser(description='hubguard — 记忆中枢并发治理')
    ap.add_argument('--db', default=None, help='真源库路径（默认取 MEM_DB）')
    sub = ap.add_subparsers(dest='cmd')

    # `shared` 与 status/doctor 同类：只读体检报告，故同组注册（自动带 --json）。
    #   ★它刻意**不依赖 --db**：查的是"跨客户端共享的注入槽位"（文件系统层面），
    #     与真源库无关；传了 --db 也不会去连库。
    for n in ('status', 'doctor', 'gen', 'shared'):
        s = sub.add_parser(n)
        s.add_argument('--json', action='store_true')

    lk = sub.add_parser('lock'); lk.add_argument('--timeout', type=float, default=None)
    lk.add_argument('--agent', default=None)
    lk.add_argument('rest', nargs=argparse.REMAINDER)

    an = sub.add_parser('annotate')
    an.add_argument('--proj', default=None, help='目标投影（默认取 MEM_PROJ_PATH 或用户级投影）')
    an.add_argument('--revert', action='store_true')
    an.add_argument('--dry-run', action='store_true')
    an.add_argument('--allow-partial', action='store_true')

    jn = sub.add_parser('journal'); jn.add_argument('--tail', type=int, default=0)
    jn.add_argument('--note', default='')

    sub.add_parser('selftest')

    sn = sub.add_parser('snapshot'); sn.add_argument('path')
    vf = sub.add_parser('verify'); vf.add_argument('path'); vf.add_argument('--sha256', required=True)

    hd = sub.add_parser('_hold')
    hd.add_argument('seconds', type=float)
    hd.add_argument('--lock', action='store_true')
    hd.add_argument('--no-lock', dest='lock', action='store_false')
    hd.add_argument('--wrapped', action='store_true', help='走 install_guards 包装后的写入函数')
    hd.add_argument('--timeout', type=float, default=5.0)
    hd.set_defaults(lock=True)

    ap2 = sub.add_parser('_append')
    ap2.add_argument('n', type=int)
    ap2.add_argument('path')
    ap2.add_argument('tag')

    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        return 0
    # ★必须把 --db 提升成 MEM_DB 环境变量，不能只留在局部变量里：
    #   install_guards 包装后的函数内部拿锁走的是 `db_path(None)`，
    #   只认「显式参数 → MEM_DB → HUB/memory.db → 发现」这条链。
    #   若只设局部 db，就会出现「父进程锁 A 库、子进程锁 B 库」→ 本该被挡却没被挡
    #   → 自检**假通过**。实测踩到：D3 报 child_blocked=false，根因就是这个。
    if a.db:
        os.environ['MEM_DB'] = a.db
        _DB_CACHE.clear()
    db = a.db

    if a.cmd == 'status':
        status(db, a.json)
    elif a.cmd == 'doctor':
        r = doctor(db, a.json)
        return 1 if r['verdict'] == 'fail' else 0
    elif a.cmd == 'gen':
        fp = db_fingerprint(db)
        print(json.dumps(fp, ensure_ascii=False, indent=2) if a.json else fp.get('fp'))
    elif a.cmd == 'shared':
        # ★只读；退出码必须真的传出去（有路径读不到 = 1），否则脚本里 `&&` 会继续往下跑。
        return shared_report(a.json)
    elif a.cmd == 'lock':
        rest = [x for x in a.rest if x != '--']
        if not rest:
            print('用法：hubguard.py lock -- <命令...>', file=sys.stderr)
            return 2
        with hub_lock(timeout=a.timeout, agent=a.agent, purpose=' '.join(rest)[:60], db=db) as info:
            sys.stderr.write('[hubguard] 已持锁 %s（pid=%s）\n' % (info['path'], os.getpid()))
            p = subprocess.run(rest, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return p.returncode
    elif a.cmd == 'annotate':
        target = a.proj or proj_paths()[0]
        before = snapshot(target)
        if not before.get('exists'):
            print('目标不存在：%s' % target, file=sys.stderr)
            return 2
        text = _read_text(target)
        try:
            new, stats = annotate_projection(text, db, allow_partial=a.allow_partial,
                                             revert=a.revert)
        except RuntimeError as e:
            print('[annotate] %s' % e, file=sys.stderr)
            return 1
        stats['chars_before'] = len(text)
        stats['chars_after'] = len(new)
        stats['delta'] = len(new) - len(text)
        stats['budget'] = proj_budget()
        stats['headroom_after'] = proj_budget() - len(new)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        if a.dry_run:
            print('[dry-run] 未写入', file=sys.stderr)
            return 0
        with hub_lock(agent=os.environ.get('MEM_AGENT') or 'hubguard',
                      purpose='annotate', db=db):
            try:
                r = commit_guarded(target, new, before, on_conflict='abort')
            except ConcurrentModification as e:
                print('[annotate] %s' % e, file=sys.stderr)
                return 1
        print(json.dumps({k: r[k] for k in ('ok', 'path', 'atomic', 'conflict') if k in r},
                         ensure_ascii=False))
        return 0
    elif a.cmd == 'journal':
        if a.tail:
            jp = journal_path(db)
            try:
                with open(jp, encoding='utf-8') as f:
                    lines = [x for x in f.read().splitlines() if x.strip()]
            except Exception:
                lines = []
            for ln in lines[-a.tail:]:
                try:
                    e = json.loads(ln)
                except Exception:
                    continue
                w = ' · '.join('%s×%d' % (x.get('source'), x.get('n', 0))
                               for x in (e.get('writers') or [])[:3])
                print('%s  %s  facts=%s(%+d)  %s' %
                      (e.get('ts'), e.get('fp'), e.get('facts_n'),
                       e.get('delta_facts') or 0, w))
            return 0
        e = journal_record(db, a.note)
        print(json.dumps(e, ensure_ascii=False))
    elif a.cmd == 'selftest':
        return 0 if selftest(db) else 1
    elif a.cmd == 'snapshot':
        print(json.dumps(snapshot(a.path), ensure_ascii=False, indent=2))
    elif a.cmd == 'verify':
        cur = snapshot(a.path)
        ok = (cur.get('sha256') == a.sha256)
        print(json.dumps({'ok': ok, 'path': a.path, 'expect': a.sha256,
                          'actual': cur.get('sha256'), 'size': cur.get('size')},
                         ensure_ascii=False))
        return 0 if ok else 1
    elif a.cmd == '_hold':
        _hold_child(a.seconds, a.lock, db, a.timeout, wrapped=a.wrapped)
    elif a.cmd == '_append':
        _append_child(a.n, a.path, a.tag)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
