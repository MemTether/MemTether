# PROJ-JUDGE Report
> 2026-10-13 16:51:10

## L1 硬闸（一票否决）
- ✅ pytest (rc=0)
- ✅ make_jury (rc=0)
- ❌ check_packaging (rc=1)
  - ★打包清单与仓库不一致：
   - 清单缺这些模块（wheel 里会没有它们）：proj_judge

   改 pyproject.toml 或 NOTICE 后重跑本脚本。

- ✅ ruff (rc=0)
- ✅ scan_leaks (rc=0)

**L1 FAILED: ['check_packaging'] — 一票否决，不再计分**