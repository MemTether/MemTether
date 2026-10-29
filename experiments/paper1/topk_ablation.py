"""E3: Top-k ablation with 100-question stratified samples."""
import subprocess, json, shutil, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PY = "E:/RUANJIAN/memory_hub/.venv-memory/Scripts/python.exe"
BENCH = "E:/RUANJIAN/memory_hub/bench_longmemeval.py"
OUT = "E:/Temp"

env = os.environ.copy()
env["MEM_BUMP"] = "0"
env["PYTHONIOENCODING"] = "utf-8"

results = {}
for k in [5, 10, 15, 20, 30]:
    outfile = f"{OUT}/bench_topk_{k}.json"
    if os.path.exists(outfile):
        print(f"k={k} already done, skipping")
        results[k] = json.load(open(outfile, encoding="utf-8"))
        continue
    
    print(f"\n{'='*60}")
    print(f"Running k={k} (100 questions, no-llm for strict only)...")
    
    r = subprocess.run(
        [PY, BENCH, "--sample", "100", "--variant", "oracle", "--k", str(k), "--no-llm"],
        capture_output=True, text=True, timeout=3600,
        encoding="utf-8", errors="replace",
        cwd="E:/RUANJIAN/memory_hub",
        env=env
    )
    
    # Copy result
    src = "E:/RUANJIAN/memory_hub/bench_longmemeval_result.json"
    shutil.copy(src, outfile)
    d = json.load(open(outfile, encoding="utf-8"))
    results[k] = d
    print(f"k={k}: strict={d.get('strict')} ({d.get('strict_n')} scored)")
    
    # Print per-type
    for t, v in d.get("per_type", {}).items():
        sn = v.get("strict_n", 0)
        if sn:
            print(f"  {t}: strict={v.get('strict',0)}/{sn}={v.get('strict',0)/sn*100:.1f}%")

# Summary
print(f"\n{'='*60}")
print("TOP-K SUMMARY (strict only)")
print(f"{'k':<6} {'strict':<10} {'strict_n':<10} {'score':<8}")
for k in [5, 10, 15, 20, 30]:
    if k in results:
        d = results[k]
        s = d.get("strict", 0) or 0
        sn = d.get("strict_n", 0) or 0
        pct = s / sn * 100 if sn else 0
        print(f"{k:<6} {s:<10} {sn:<10} {pct:.1f}%")

# Save summary
with open(f"{OUT}/topk_summary.json", "w") as f:
    json.dump({str(k): {"strict": results[k].get("strict"), "strict_n": results[k].get("strict_n"),
                         "per_type": results[k].get("per_type", {})} for k in results}, f, indent=2)
print("Done!")
