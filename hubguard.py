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

_LINE_RE = None
import time

HUB = os.path.dirname(os.path.abspath(__file__))

# ============================================================================
# 路径与配置
# ============================================================================

_DB_CACHE = {}


# --- DB path discovery (moved to hubguard_local.py for local development) ---
# For pip users: MEM_DB env var or default memory.db in package dir

def set_test_db(path):
    """P3-2 (2026-10-05): explicit test override (replaces implicit
    follow-gateway sniffing, which silently overrode MEM_DB env).
    Call from test _setup_db; cleared automatically by clear_test_db."""
    global _TEST_DB
    _TEST_DB = str(path)
    return _TEST_DB

def clear_test_db():
    global _TEST_DB
    _TEST_DB = None


_TEST_DB = None

def db_path(explicit=None):
    """Resolve DB path: explicit > MEM_DB env > package-dir/memory.db.
    P1-2 (2026-10-05): follow a re-pointed gateway.DB (test harnesses patch it
    after import; without this the wrapper locked a different file than the write
    went to).
    """
    import os as _os
    if explicit:
        return explicit
    if _TEST_DB:
        return _TEST_DB

    env = _os.environ.get('MEM_DB')
    if env:
        return env
    return _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'memory.db')

def lock_path(db=None):
    p = db_path(db)
    # P1-2 (2026-10-05): in-memory DBs cannot take a file lock - skip.
    if p == ':memory:':
        return None
    return p + '.lock'

def holder_path(lp):
    return lp + '.holder'

def journal_path(db=None):
    return db_path(db) + '.journal'

def proj_paths():
    return []

def proj_budget():
    return {}

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
    if lp is None:  # P1-2: :memory: has no file lock
        return None
    if timeout is None:
        timeout = float(os.environ.get('MEM_LOCK_TIMEOUT', '120'))
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
    if lp is None:  # P1-2: :memory: has no lock file
        return {'path': None, 'exists': False, 'held': False, 'holder': None, 'stale_holder': False}
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
    p = db_path(db)
    out = {'path': p, 'origin': 'explicit' if db else 'env-or-default', 'exists': os.path.exists(p)}
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


def _wrap(func, name, timeout):
    def wrapper(*a, **k):
        with hub_lock(timeout=timeout, purpose='gateway.%s' % name):
            return func(*a, **k)
    wrapper._hub_guarded = True
    wrapper.__name__ = getattr(func, '__name__', name)
    wrapper.__doc__ = getattr(func, '__doc__', None)
    return wrapper


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




_GUARD_NAMES = ('remember', 'correct', 'retire', 'record_tool', 'search', 'rebuild', 'incident', 'on_miss')


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


