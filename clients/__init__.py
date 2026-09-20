# -*- coding: utf-8 -*-
"""clients — 各 AI 客户端的 MCP 接入适配器

分层
----
  base.py      适配器基类 + 原子写 / 备份 / 幂等 / 失败关闭
  jsonc.py     JSONC 感知的「外科式」配置编辑器（保住注释与格式）
  trust.py     Electron 系客户端的 MCP 信任免 GUI 代写
  standard.py  通用客户端（Claude / Cursor / VS Code / Zed / Cline … 共 17 家）
  local.py     本机这一套（WorkBuddy 双版本 / CodeBuddy / ZCode / dsh / OpenClaw / 中立真源）

用法
----
    from clients import all_adapters, find
    for a in all_adapters():
        for scope, path in a.candidates():
            installed, reason = a.probe(scope, path)
            ...
"""
from .base import Adapter, ServerSpec, Target, Change, BACKUP_ROOT, atomic_write, backup  # noqa
from . import jsonc, trust  # noqa
from . import standard, local  # noqa

__all__ = ["Adapter", "ServerSpec", "Target", "Change", "all_adapters", "find",
           "jsonc", "trust", "BACKUP_ROOT"]


def all_adapters():
    """全部适配器（通用在前，本机专属在后）。"""
    return list(standard.STANDARD) + list(local.LOCAL)


def find(client_id: str):
    """按 id 找适配器；支持模糊（唯一前缀/子串匹配）。"""
    ads = all_adapters()
    for a in ads:
        if a.id == client_id:
            return a
    hit = [a for a in ads if client_id and client_id in a.id]
    if len(hit) == 1:
        return hit[0]
    return None


def ids():
    return [a.id for a in all_adapters()]
