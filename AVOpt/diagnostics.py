"""确定性内核的响应面近似诊断，不代表田间预测精度。"""
import numpy as np
from . import doe

def fit_report(U, y, active, best_u):
    from scipy.stats import f as f_dist, t as t_dist
    y = np.asarray(y, dtype=float)
    A = doe.phi(U, active)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    rank = int(np.linalg.matrix_rank(A))
    n, p = A.shape
    residual = y - A @ coef
    sse = float(residual @ residual)
    sst = float(np.sum((y - y.mean()) ** 2))
    df_res = n - rank
    mse = sse / df_res if df_res > 0 else None
    inv = np.linalg.pinv(A.T @ A)
    hat = np.einsum("ij,jk,ik->i", A, inv, A)
    # 真正按留一重新拟合的等价 PRESS 公式；不可估计时不编造数值。
    cv_rmse = float(np.sqrt(np.mean((residual / (1 - hat)) ** 2))) if np.all(hat < 1 - 1e-8) else None
    unique, group = np.unique(U, axis=0, return_inverse=True)
    pure = sum(float(np.sum((y[group == i] - y[group == i].mean()) ** 2)) for i in range(len(unique)))
    df_pe = n - len(unique)
    df_lof = len(unique) - rank
    lof_p = None
    if df_pe > 0 and df_lof > 0 and pure > 1e-12 * max(sst, 1):
        lof_p = float(f_dist.sf(max(sse - pure, 0) / df_lof / (pure / df_pe), df_lof, df_pe))
    model_df = max(rank - 1, 0)
    f_value = ((max(sst - sse, 0) / model_df) / mse) if model_df and mse and mse > 1e-12 else None
    model_p = float(f_dist.sf(f_value, model_df, df_res)) if f_value is not None else None
    a0 = doe.phi(best_u, active)[0]
    interval = None
    if mse is not None and rank == p:
        center = float(a0 @ coef)
        half = float(t_dist.ppf(.975, df_res) * np.sqrt(mse * (1 + a0 @ inv @ a0)))
        interval = [center - half, center + half]
    return {"n": n, "parameters": p, "rank": rank, "rmse": float(np.sqrt(sse / n)),
            "loo_rmse": cv_rmse, "adjusted_r2": 1 - (sse / df_res) / (sst / (n - 1)) if df_res and sst > 1e-12 else None,
            "anova": [{"term": "回归", "df": model_df, "ss": max(sst - sse, 0), "f": f_value, "p": model_p},
                      {"term": "残差", "df": df_res, "ss": sse}, {"term": "总计", "df": n - 1, "ss": sst}],
            "lack_of_fit_p": lof_p, "pure_error_df": df_pe, "pure_error_ss": pure,
            "lack_of_fit_note": "确定性重复中心点的纯误差为零或自由度不足时，失拟 p 值不可估计。",
            "prediction_interval_95": interval, "surrogate_at_best": float(a0 @ coef),
            "note": "ANOVA 与区间依赖独立同方差残差假设，仅诊断响应面近似内核的误差，不代表气象、模型或田间不确定性。"}

def sensitivity(ev, best, bounds, active):
    values = [best[k] for k in ("tilt", "span", "height", "gap")]
    rows = []
    for i in active:
        step = .1 * max(abs(values[i]), bounds[i][1] - bounds[i][0])
        for direction in (-1, 1):
            point = list(values)
            point[i] = float(np.clip(values[i] + direction * step, *bounds[i]))
            if abs(point[i] - values[i]) < 1e-10:
                continue
            r = ev.evaluate(*point)
            ck = best["yield_ck"]
            rows.append({"factor": ["tilt", "s", "h", "g"][i], "value": point[i], "base_value": values[i],
                         "net_change": r["net"] - best["net"],
                         "yield_pct_change": 100 * (r["yield"] - best["yield"]) / ck if ck > 0 else None})
    return rows
