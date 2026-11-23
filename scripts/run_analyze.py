import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats

df = pd.read_csv("full_dataset.csv")
df["log_LOC"] = np.log1p(df["LOC"])
df["log_Churn"] = np.log1p(df["CodeChurn"])
df["log_Dev"] = np.log1p(df["DevCount"])
df["log_Age"] = np.log1p(df["ModuleAge"])
df["CD_Type2_3"] = df["CD_Type2"] + df["CD_Type3"]

print("=== 1. ОПИСОВА СТАТИСТИКА (N = 431) ===")
cols = ["CD_total", "CD_Type1", "CD_Type2", "CD_Type3", "LOC", "PhysicalLOC", "CodeChurn", "DevCount", "ModuleAge", "BugFixFreq_T1"]
desc = pd.DataFrame({
    "Min": df[cols].min(),
    "Q1": df[cols].quantile(0.25),
    "Median": df[cols].median(),
    "Mean": df[cols].mean(),
    "Q3": df[cols].quantile(0.75),
    "IQR": df[cols].quantile(0.75) - df[cols].quantile(0.25),
    "Max": df[cols].max(),
    "SD": df[cols].std(),
    "Zero_%": (df[cols] == 0).mean() * 100
})
print(desc.round(4))

print("\n=== 2. КОРЕЛЯЦІЇ СПІРМЕНА З BugFixFreq_T1 ===")
for c in ["CD_total", "CD_Type1", "CD_Type2", "CD_Type3", "LOC", "CodeChurn", "DevCount", "ModuleAge"]:
    rho, p = stats.spearmanr(df[c], df["BugFixFreq_T1"])
    print(f"{c:12s}: rho = {rho:+.4f}, p = {p:.4f}")

def fit_nb_glm(formula, data):
    pois = smf.glm(formula, data=data, family=sm.families.Poisson()).fit()
    y = np.asarray(data["BugFixFreq_T1"])
    mu = np.asarray(pois.mu)
    aux_y = ((y - mu) ** 2 - y) / mu
    alpha = max(0.05, float(np.linalg.lstsq(mu.reshape(-1, 1), aux_y, rcond=None)[0][0]))
    nb = smf.glm(formula, data=data, family=sm.families.NegativeBinomial(alpha=alpha)).fit()
    res = pd.DataFrame({
        "beta": nb.params,
        "SE": nb.bse,
        "z": nb.tvalues,
        "p_value": nb.pvalues,
        "IRR": np.exp(nb.params),
        "CI_low": np.exp(nb.conf_int()[0]),
        "CI_high": np.exp(nb.conf_int()[1]),
    })
    return nb, alpha, res

print("\n=== 3. NEGATIVE BINOMIAL GLM: H1 (CD_total) ===")
nb1, alpha1, res1 = fit_nb_glm("BugFixFreq_T1 ~ CD_total + log_LOC + log_Churn + log_Dev + log_Age", df)
print(f"alpha = {alpha1:.4f}, LLF = {nb1.llf:.4f}, AIC = {nb1.aic:.4f}")
print(res1.round(4))

print("\n=== 4. NEGATIVE BINOMIAL GLM: H2 (CD_Type1, CD_Type2, CD_Type3) ===")
nb2, alpha2, res2 = fit_nb_glm("BugFixFreq_T1 ~ CD_Type1 + CD_Type2 + CD_Type3 + log_LOC + log_Churn + log_Dev + log_Age", df)
print(f"alpha = {alpha2:.4f}, LLF = {nb2.llf:.4f}, AIC = {nb2.aic:.4f}")
print(res2.round(4))

print("\n=== 5. ПОРОГОВИЙ АНАЛІЗ H3 (CD_Type2_3) ===")
bins = [-0.001, 0.0, 0.25, 0.50, 0.75, 1.001]
labels = ["0%", "(0%; 25%]", "(25%; 50%]", "(50%; 75%]", "(75%; 100%]"]
df["bin"] = pd.cut(df["CD_Type2_3"], bins=bins, labels=labels)
grp = df.groupby("bin", observed=False).agg(
    n=("BugFixFreq_T1", "count"),
    bugs_sum=("BugFixFreq_T1", "sum"),
    bugs_mean=("BugFixFreq_T1", "mean"),
    bugs_sd=("BugFixFreq_T1", "std"),
    buggy_n=("BugFixFreq_T1", lambda x: (x > 0).sum()),
    buggy_pct=("BugFixFreq_T1", lambda x: (x > 0).mean() * 100),
    med_loc=("LOC", "median")
)
print(grp.round(4))

groups_kw = [g["BugFixFreq_T1"].values for _, g in df.groupby("bin", observed=False)]
kw_stat, kw_p = stats.kruskal(*groups_kw)
print(f"Kruskal-Wallis omnibus test: H = {kw_stat:.4f}, p = {kw_p:.4f}")

for thr in [0.0, 0.20, 0.25, 0.30, 0.50]:
    low = df[df["CD_Type2_3"] <= thr]["BugFixFreq_T1"]
    high = df[df["CD_Type2_3"] > thr]["BugFixFreq_T1"]
    u, p = stats.mannwhitneyu(low, high, alternative="two-sided")
    print(f"Mann-Whitney thr={thr:.2f}: n_low={len(low)} (mean={low.mean():.4f}), n_high={len(high)} (mean={high.mean():.4f}), U={u:.1f}, p={p:.4f}")

print("\n=== 6. ПЕРЕВІРКА СТІЙКОСТІ ===")
q95_loc, q95_churn = df["LOC"].quantile(0.95), df["CodeChurn"].quantile(0.95)
df_no_out = df[(df["LOC"] <= q95_loc) & (df["CodeChurn"] <= q95_churn)]
_, _, res_sub = fit_nb_glm("BugFixFreq_T1 ~ CD_total + log_LOC + log_Churn + log_Dev + log_Age", df_no_out)
print(f"Без топ-5% викидів (N={len(df_no_out)}):")
print(res_sub[["beta", "SE", "p_value", "IRR"]].round(4))

df_java = df[df["project"] != "Textualize/rich"]
_, _, res_java = fit_nb_glm("BugFixFreq_T1 ~ CD_total + log_LOC + log_Churn + log_Dev + log_Age", df_java)
print(f"\nЛише Java-проєкти (N={len(df_java)}):")
print(res_java[["beta", "SE", "p_value", "IRR"]].round(4))

# Побудова оновленого Рисунка 1
plt.rcParams["font.family"] = "DejaVu Sans"
fig, ax1 = plt.subplots(figsize=(11.5, 6.0), dpi=300)
x = np.arange(len(labels))
x_ticks = [f"{lbl}\n[n = {int(n)}]" for lbl, n in zip(labels, grp["n"])]
mean_vals = grp["bugs_mean"].values
pct_vals = grp["buggy_pct"].values

bars1 = ax1.bar(x - 0.16, mean_vals, width=0.32, color=["#4A777A", "#5B8FB9", "#D97724", "#E08E45", "#C85A32"],
                edgecolor="#1F2933", label="Середня частота виправлень (BugFixFreq_T₁)", zorder=3)
ax1.set_ylabel("Середня кількість bug-fix комітів на модуль", fontsize=10.5, fontweight="bold")
ax1.set_ylim(0, max(mean_vals) * 1.45)
ax1.set_xticks(x)
ax1.set_xticklabels(x_ticks, fontsize=9.5)
ax1.grid(axis="y", linestyle=":", alpha=0.6, zorder=0)
for b, v in zip(bars1, mean_vals):
    ax1.text(b.get_x() + b.get_width() / 2, v + max(mean_vals) * 0.02, f"{v:.4f}", ha="center", va="bottom", fontsize=8.8, fontweight="bold")

ax2 = ax1.twinx()
bars2 = ax2.bar(x + 0.16, pct_vals, width=0.32, color="#CBD5E1", edgecolor="#475569", hatch="//",
                label="Частка модулів із дефектами (%)", zorder=3)
ax2.set_ylabel("Частка модулів із BugFixFreq_T₁ > 0 (%)", fontsize=10.5, fontweight="bold", color="#475569")
ax2.set_ylim(0, max(pct_vals) * 1.45)
for b, v in zip(bars2, pct_vals):
    ax2.text(b.get_x() + b.get_width() / 2, v + max(pct_vals) * 0.02, f"{v:.2f}%", ha="center", va="bottom", fontsize=8.8, color="#334155")

ax1.axvline(x=1.5, color="#B91C1C", linestyle="--", linewidth=2.2, zorder=5)
ax1.axvspan(1.5, 4.5, color="#FEE2E2", alpha=0.22, zorder=1)
plt.title("Залежність частоти виправлень дефектів від інтервалів щільності клонів (CD_Type-2/3, N = 431)", fontsize=11.5, fontweight="bold", pad=14)
h1, l1 = ax1.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax1.legend(h1 + h2, l1 + l2, loc="upper left", frameon=True, facecolor="white", fontsize=9.0)
plt.tight_layout()
plt.savefig("figure1_quality_gates_lr7.png", dpi=300, bbox_inches="tight", facecolor="white")
print("\nОновлений графік збережено у figure1_quality_gates_lr7.png")