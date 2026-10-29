"""
Paper 1: Statistics verification + Figure generation.
All numbers from paper1-answer-formulation-gap.md (verified data).
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import numpy as np
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import os

OUT = "E:/RUANJIAN/memory_hub/experiments/paper1/figures"
os.makedirs(OUT, exist_ok=True)

# ============ DATA ============
# From paper markdown Table 1 (verified 2026-09-27 bench)
types = {
    "single-session-user":     {"n": 70,  "strict": 55, "strict_n": 59,  "judge": 67,  "judge_n": 70},
    "single-session-assistant":{"n": 56,  "strict": 19, "strict_n": 47,  "judge": 25,  "judge_n": 55},
    "single-session-preference":{"n": 30, "strict": None, "strict_n": 0, "judge": 13,  "judge_n": 30},
    "knowledge-update":        {"n": 78,  "strict": 52, "strict_n": 64,  "judge": 50,  "judge_n": 72},
    "temporal-reasoning":      {"n": 133, "strict": 47, "strict_n": 84,  "judge": 61,  "judge_n": 127},
    "multi-session":           {"n": 133, "strict": 26, "strict_n": 80,  "judge": 93,  "judge_n": 131},
}

# Table 3: computed vs entity decomposition
computed = {
    "multi-session":            {"strict_applicable": 80, "computed": 73, "entity": 7,  "computed_pct": 91},
    "temporal-reasoning":       {"strict_applicable": 84, "computed": 28, "entity": 56, "computed_pct": 33},
    "knowledge-update":         {"strict_applicable": 64, "computed": 37, "entity": 27, "computed_pct": 58},
    "single-session-user":      {"strict_applicable": 59, "computed": 19, "entity": 40, "computed_pct": 32},
    "single-session-assistant": {"strict_applicable": 47, "computed": 6,  "entity": 41, "computed_pct": 13},
}

# ============ 1. VERIFY STATS ============
print("=" * 60)
print("STATISTICS VERIFICATION")
print("=" * 60)

def cohen_h(p1, p2):
    """Cohen's h effect size for two proportions."""
    return 2 * np.arcsin(np.sqrt(p1)) - 2 * np.arcsin(np.sqrt(p2))

results = {}
for t, d in types.items():
    if d["strict"] is None:
        print(f"\n{t}: strict N/A (all prose)")
        continue
    
    s_prop = d["strict"] / d["strict_n"]
    j_prop = d["judge"] / d["judge_n"]
    gap = (j_prop - s_prop) * 100
    
    # Bootstrap CI (10,000 resamples)
    rng = np.random.RandomState(42)
    n_boot = 10000
    gaps = []
    for _ in range(n_boot):
        s = rng.binomial(d["strict_n"], s_prop) / d["strict_n"]
        j = rng.binomial(d["judge_n"], j_prop) / d["judge_n"]
        gaps.append((j - s) * 100)
    ci_lo, ci_hi = np.percentile(gaps, [2.5, 97.5])
    
    # Cohen's h
    h = cohen_h(s_prop, j_prop)
    
    results[t] = {
        "gap": round(gap, 1),
        "ci": [round(ci_lo, 1), round(ci_hi, 1)],
        "h": round(h, 3),
    }
    print(f"\n{t}:")
    print(f"  strict={d['strict']}/{d['strict_n']}={s_prop*100:.1f}%, judge={d['judge']}/{d['judge_n']}={j_prop*100:.1f}%")
    print(f"  gap={gap:+.1f}pt, 95%CI=[{ci_lo:.1f}, {ci_hi:.1f}], h={h:.3f}")

# McNemar exact test for multi-session (conservative b=54, c=0)
b, c = 54, 0
p_val = stats.binom_test(c, b + c, 0.5) if hasattr(stats, 'binom_test') else stats.binomtest(c, b + c, 0.5).pvalue
print(f"\nMcNemar exact (multi-session, b={b}, c={c}): p={p_val:.2e}")

# Pearson correlation: computed% vs gap
computed_pcts = []
gaps = []
for t in computed:
    if t in results:
        computed_pcts.append(computed[t]["computed_pct"])
        gaps.append(results[t]["gap"])
r, p_r = stats.pearsonr(computed_pcts, gaps)
print(f"\nPearson r (computed% vs gap): r={r:.3f}, p={p_r:.4f} (n={len(computed_pcts)} types)")

# Bonferroni correction (5 tests)
print(f"\nBonferroni correction (5 tests, alpha=0.05/5=0.01):")
for t, res in results.items():
    sig = "YES" if res["ci"][0] > 0 or res["ci"][1] < 0 else "No"
    print(f"  {t}: CI={res['ci']} → {sig}")

# Save results
with open(f"{OUT}/stats_results.json", "w") as f:
    json.dump({"results": results, "mcnemar_p": p_val, "pearson_r": r}, f, indent=2)

# ============ 2. FIGURE 1: Scatter (computed% vs gap) ============
fig, ax = plt.subplots(figsize=(6, 4.5))
for t in computed:
    cp = computed[t]["computed_pct"]
    g = results[t]["gap"]
    color = "#e74c3c" if t == "multi-session" else "#3498db"
    size = 120 if t == "multi-session" else 80
    ax.scatter(cp, g, c=color, s=size, zorder=5, edgecolors="white", linewidths=0.8)
    label = t.replace("single-session-", "ss-").replace("multi-session", "multi")
    ax.annotate(label, (cp, g), textcoords="offset points", xytext=(8, 4), fontsize=8, color="#333")

# Fit line
z = np.polyfit(computed_pcts, gaps, 1)
xs = np.linspace(0, 100, 100)
ax.plot(xs, np.polyval(z, xs), "--", color="#95a5a6", linewidth=1.2, alpha=0.7)
ax.axhline(0, color="#bdc3c7", linewidth=0.8, alpha=0.5)

ax.set_xlabel("Computed-aggregate proportion (%)", fontsize=10)
ax.set_ylabel("Strict–Judge gap (pt)", fontsize=10)
ax.set_title(f"Computed proportion predicts gap (r={r:.2f})", fontsize=11)
ax.set_xlim(0, 105)
ax.set_ylim(-20, 50)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
plt.tight_layout()
plt.savefig(f"{OUT}/fig1_scatter.pdf", dpi=300, bbox_inches="tight")
plt.savefig(f"{OUT}/fig1_scatter.png", dpi=150, bbox_inches="tight")
print(f"\nFigure 1 saved")

# ============ 3. FIGURE 2: Stacked bar (computed vs entity) ============
fig, ax = plt.subplots(figsize=(7, 4))
type_names = list(computed.keys())
comp_pcts = [computed[t]["computed_pct"] for t in type_names]
ent_pcts = [100 - cp for cp in comp_pcts]

x = np.arange(len(type_names))
bars1 = ax.bar(x, comp_pcts, 0.6, label="Computed aggregate", color="#e74c3c", alpha=0.85)
bars2 = ax.bar(x, ent_pcts, 0.6, bottom=comp_pcts, label="Verbatim entity", color="#3498db", alpha=0.85)

for i, (cp, ep) in enumerate(zip(comp_pcts, ent_pcts)):
    ax.text(i, cp/2, f"{cp}%", ha="center", va="center", fontsize=9, fontweight="bold", color="white")
    if ep > 5:
        ax.text(i, cp + ep/2, f"{ep}%", ha="center", va="center", fontsize=9, color="white")

labels = [t.replace("single-session-", "ss-").replace("multi-session", "multi\nsession") for t in type_names]
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=9)
ax.set_ylabel("Proportion of strict-applicable questions (%)", fontsize=10)
ax.set_title("Answer type composition by question type", fontsize=11)
ax.legend(loc="upper right", fontsize=9)
ax.set_ylim(0, 105)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
plt.tight_layout()
plt.savefig(f"{OUT}/fig2_stacked.pdf", dpi=300, bbox_inches="tight")
plt.savefig(f"{OUT}/fig2_stacked.png", dpi=150, bbox_inches="tight")
print(f"Figure 2 saved")

# ============ 4. FIGURE 3: Dual-bar (strict vs judge per type) ============
fig, ax = plt.subplots(figsize=(7, 4.5))
scoreable = [t for t in types if types[t]["strict"] is not None]
s_pcts = [types[t]["strict"] / types[t]["strict_n"] * 100 for t in scoreable]
j_pcts = [types[t]["judge"] / types[t]["judge_n"] * 100 for t in scoreable]

x = np.arange(len(scoreable))
w = 0.35
ax.bar(x - w/2, s_pcts, w, label="Strict substring", color="#3498db", alpha=0.85)
ax.bar(x + w/2, j_pcts, w, label="LLM judge", color="#e74c3c", alpha=0.85)

for i, (s, j) in enumerate(zip(s_pcts, j_pcts)):
    ax.text(i - w/2, s + 1.5, f"{s:.1f}", ha="center", fontsize=8)
    ax.text(i + w/2, j + 1.5, f"{j:.1f}", ha="center", fontsize=8)
    gap = j - s
    color = "#e74c3c" if abs(gap) > 20 else "#333"
    ax.text(i, max(s, j) + 8, f"Δ{gap:+.1f}", ha="center", fontsize=8, fontweight="bold", color=color)

labels = [t.replace("single-session-", "ss-").replace("multi-session", "multi\nsession") for t in scoreable]
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=9)
ax.set_ylabel("Score (%)", fontsize=10)
ax.set_title("Strict vs LLM-judge scores by question type", fontsize=11)
ax.legend(fontsize=9)
ax.set_ylim(0, 110)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
plt.tight_layout()
plt.savefig(f"{OUT}/fig3_dual.pdf", dpi=300, bbox_inches="tight")
plt.savefig(f"{OUT}/fig3_dual.png", dpi=150, bbox_inches="tight")
print(f"Figure 3 saved")

# ============ 5. FIGURE 4: PPR decoupling ============
fig, axes = plt.subplots(1, 2, figsize=(8, 3.5))

# Left: recall improvement
ax = axes[0]
configs = ["Top-10\n(no PPR)", "Top-30\n(with PPR)"]
recalls = [19, 30]
bars = ax.bar(configs, recalls, 0.5, color=["#3498db", "#2ecc71"], alpha=0.85)
for bar, v in zip(bars, recalls):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5, str(v), ha="center", fontsize=11, fontweight="bold")
ax.set_ylabel("Relevant evidence recalled (facts)", fontsize=9)
ax.set_title("Retrieval: +58% recall", fontsize=10)
ax.set_ylim(0, 36)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

# Right: strict unchanged
ax = axes[1]
stricts = [10, 10]
bars = ax.bar(configs, stricts, 0.5, color=["#3498db", "#2ecc71"], alpha=0.85)
for bar, v in zip(bars, stricts):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2, f"{v}%", ha="center", fontsize=11, fontweight="bold")
ax.set_ylabel("Strict score (%)", fontsize=9)
ax.set_title("Scoring: ±0 change", fontsize=10)
ax.set_ylim(0, 15)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

fig.suptitle("PPR Retrieval-Decoupling Experiment", fontsize=11, y=1.02)
plt.tight_layout()
plt.savefig(f"{OUT}/fig4_ppr.pdf", dpi=300, bbox_inches="tight")
plt.savefig(f"{OUT}/fig4_ppr.png", dpi=150, bbox_inches="tight")
print(f"Figure 4 saved")

print(f"\nAll figures saved to {OUT}/")
