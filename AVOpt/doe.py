"""响应面试验设计与寻优（支持 0～4 个待优化变量）。

设计变量为组件倾角 β、阵列跨度 S、安装高度 H、横向间隙 G，依次对应下标 0～3。
用户可固定其中任意项，只对未固定的变量做试验设计与寻优；全部固定时退化为单方案评估。
"""
import itertools
import numpy as np

NVAR = 4


def design(active, levels=5):
    """生成编码设计点。

    统一五水平：-1、-0.5、0、0.5、1。
    3/4 因子沿用两两配对设计并附加 5 个中心点；四因子共 155 组。
    levels 参数保留以兼容旧调用，旧设置统一迁移到 5。
    """
    levels = 5
    k = len(active)
    if k == 0:
        return np.zeros((1, NVAR), dtype=float)
    if k == 1:
        grid = np.linspace(-1, 1, max(levels, 5))
        rows = []
        for v in grid:
            row = [0.0] * NVAR
            row[active[0]] = v
            rows.append(row)
        return np.array(rows, dtype=float)
    if k == 2:
        grid = np.linspace(-1, 1, levels)
        rows = []
        for a in grid:
            for b in grid:
                row = [0.0] * NVAR
                row[active[0]], row[active[1]] = a, b
                rows.append(row)
        return np.array(rows, dtype=float)

    grid = tuple(np.linspace(-1, 1, levels))
    rows = []
    for i, j in itertools.combinations(active, 2):
        for a in grid:
            for b in grid:
                row = [0.0] * NVAR
                row[i], row[j] = a, b
                rows.append(row)
    n_center = 5
    rows += [[0.0] * NVAR] * n_center
    return np.array(rows, dtype=float)


def phi(U, active):
    """按活跃变量构造二次模型的设计矩阵。"""
    U = np.atleast_2d(U)
    cols = [np.ones(len(U))]
    for i in active:
        cols.append(U[:, i])
    for i in active:
        cols.append(U[:, i] ** 2)
    for a, b in itertools.combinations(active, 2):
        cols.append(U[:, a] * U[:, b])
    return np.column_stack(cols)


def fit(U, y, active):
    A = phi(U, active)
    coef, *_ = np.linalg.lstsq(A, np.asarray(y, dtype=float), rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 1.0
    return coef, r2


def predict(coef, U, active):
    return phi(U, active) @ coef


def coded_to_real(U, bounds):
    U = np.atleast_2d(U)
    lo = np.array([b[0] for b in bounds], dtype=float)
    hi = np.array([b[1] for b in bounds], dtype=float)
    mid = (lo + hi) / 2.0
    half = (hi - lo) / 2.0
    half[half == 0] = 1.0
    return mid + U * half


def real_to_coded(X, bounds):
    X = np.atleast_2d(X)
    lo = np.array([b[0] for b in bounds], dtype=float)
    hi = np.array([b[1] for b in bounds], dtype=float)
    mid = (lo + hi) / 2.0
    half = (hi - lo) / 2.0
    half[half == 0] = 1.0
    return (X - mid) / half


def grid_optimum(models, bounds, weights, y_ck, active, n=41, y_min_frac=0.0):
    """在活跃变量张成的网格上寻优；未激活变量取编码 0（区间中值）。"""
    axes = [np.linspace(-1, 1, n) if i in active else np.array([0.0]) for i in range(NVAR)]
    U = np.array(list(itertools.product(*axes)))
    net = predict(models["net"], U, active)
    y = predict(models["yield"], U, active)
    cv = np.clip(predict(models["cv"], U, active), 0, None)
    un = predict(models["under"], U, active)
    w = np.array(weights, dtype=float)
    w = w / max(w.sum(), 1e-9)
    net_n = (net - net.min()) / max(net.max() - net.min(), 1e-9)
    y_n = np.clip(y / max(y_ck, 1e-9), 0, 1.2)
    cv_n = 1.0 - np.clip(cv / max(cv.max(), 1e-9), 0, 1)
    score = w[0] * net_n + w[1] * y_n + w[2] * cv_n
    if y_min_frac > 0:
        score = np.where(y >= y_min_frac * y_ck, score, -1e9)
    if np.max(score) < -1e8:
        score = w[0] * net_n + w[1] * y_n + w[2] * cv_n
        feasible = np.zeros(len(U), dtype=bool)
    else:
        feasible = score > -1e8
    k = int(np.argmax(score))
    order = np.argsort(-net)
    pareto, best_y = [], -1e18
    for idx in order:
        if y[idx] > best_y:
            pareto.append(idx)
            best_y = y[idx]
    pareto = np.array(pareto[::-1])
    return {"best_u": U[k], "best_real": coded_to_real(U[k], bounds)[0],
            "best": {"net": float(net[k]), "yield": float(y[k]), "cv": float(cv[k]),
                     "under": float(un[k]), "score": float(score[k])},
            "pareto_real": coded_to_real(U[pareto], bounds),
            "pareto_net": net[pareto], "pareto_y": y[pareto],
            "feasible": bool(feasible[k]), "score": score,
            "range": (float(net.min()), float(net.max()), float(cv.max())),
            "grid": (U, net, y, cv, un)}
