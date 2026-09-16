#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asset_selfcheck.py —— 资产保鲜自检（供定时任务调用）

做三件事：
  1. 跑 tool_audit.verify()，把失活资产标 missing
  2. 跑 hub_score.score()，把分数落到 scorecard.json
  3. 若覆盖度或保鲜度 < 90%，在 facts 表写一条告警
"""
import os, sys, subprocess, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, HERE)

def main():
    log = []
    # 1) 校验
    import tool_audit
    good, bad = tool_audit.verify()
    log.append("verify: 存活%d 失活%d" % (good, bad))
    # 2) 评分
    import hub_score
    total = hub_score.score()
    log.append("score: %.1f%%" % (total * 100))
    # 3) 告警
    if total < 0.90:
        import sqlite3
        c = sqlite3.connect(os.path.join(HERE, "memory.db"))
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""INSERT INTO facts(uid,type,subject,content,status,valid_from,recorded_at,
                     temporal_source,source,scope,confidence,tags,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  ("alert-" + dt.datetime.now().strftime("%Y%m%d%H%M%S"), "incident",
                   "资产自检告警",
                   "结论：资产自检得分 %.1f%% < 90%%，需人工检查 tool_assets。" % (total * 100),
                   "active", now, now, "native", "asset_selfcheck.py", "global", 1.0,
                   '["alert"]', now, now))
        c.commit()
        log.append("ALERT 已写入 facts")
    print(" | ".join(log))
    return 0 if total >= 0.90 else 1

if __name__ == "__main__":
    sys.exit(main())
