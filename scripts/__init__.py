"""scripts —— 发布 / 自检脚本包。

为什么它必须是"包"而不是一堆散脚本
----------------------------------
`make_demo_db.py` 会被 `memtether demo` 在**运行期**调用
（`scripts.make_demo_db`），所以它要随 wheel 一起分发。
而 `pyproject.toml` 里的 `packages = ["scripts"]` 要求本目录有 `__init__.py`，
否则 setuptools 不认为它是包、直接跳过 ——
结果就是"装得上、但 `memtether demo` 找不到生成器"。

★层级是有意义的，别挪
--------------------
`make_demo_db.py` 用 `ROOT/gateway.py` 抽 SCHEMA 字面量：
  · 开发态：本文件在 `<repo>/scripts/`，ROOT = `<repo>`，gateway.py 就在那儿；
  · 安装后：本文件在 `<site-packages>/scripts/`，ROOT 恰好也是 gateway.py 所在处。
所以本包**必须**与那些平铺的根模块同层，不能挪进 `src/` 之类。
（该依赖另有 `_gateway_src()` 兜底：找不到就按模块搜索路径定位。）

★本包内的脚本可以平铺互相 import：`scan_history_leaks.py` 自己做了
  `sys.path.insert(0, HERE)`，见该文件第 28 行的说明。
"""
