# -*- coding: utf-8 -*-
"""B/S 架构的农光互补布局优化界面（标准库 http.server，无第三方 Web 框架）。"""
import base64
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
import io
import json
import os
import urllib.parse
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import webbrowser
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.ticker import MaxNLocator, PercentFormatter

from AVSim.config import ArrayConfig
from AVSim.shade import panel_post, panel_segment
from .engine import Evaluator, load_weather
from . import doe, weather_cn, inputs, diagnostics, geocoding, era5_land, open_meteo, period
from .crop_library import metadata as crop_metadata
VERSION = "1.6.0"
PLOT_LOCK = threading.Lock()
LOGGER = logging.getLogger("AVOpt")
JOB_LOCK = threading.Lock()
JOBS = {}
JOB_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="avopt-job")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
available = {font.name for font in fm.fontManager.ttflist}
families = [name for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans CJK JP", "WenQuanYi Micro Hei", "Arial Unicode MS") if name in available]
plt.rcParams["font.family"] = families + ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 160

COL = {"低侧板沿": "#536c9f", "板下": "#b5722a", "高侧板沿": "#ae841a", "板间": "#528045"}
DEFAULT_WEATHER = None


def _png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _plot_zone_means(ax, best):
    for i, (zone, color) in enumerate(COL.items()):
        if best["frac"][zone] > 0:
            val = best["zones"][zone]["T"]
            ax.bar(i, val, width=.62, color=color, alpha=.85)
            ax.text(i, min(val + .025, .965), f"{val:.1%}", ha="center", va="bottom", fontsize=9)
        else:
            ax.text(i, .04, "—", ha="center", color="#617388")
    ax.set_xticks(range(len(COL)), ["低侧\n板沿", "板下", "高侧\n板沿", "板间"])
    ax.set_xlim(-.55, len(COL) - .45)
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.grid(axis="y", alpha=.2)
    ax.spines[["top", "right"]].set_visible(False)


def _light_profile(ev, best):
    cfg = ArrayConfig(tilt=best["tilt"], height=best["height"], span=best["span"], width=ev.width)
    fig, (ax, bars) = plt.subplots(1, 2, figsize=(9.4, 3.65), layout="constrained",
                                  gridspec_kw={"width_ratios": [2.35, 1]})
    section = ax.twinx()
    section.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    low, high = panel_segment(cfg)
    panel_color = "#607e94"
    height_color = "#617388"
    zmax = high[1] * 1.28
    section.set_ylim(0, zmax)
    section.yaxis.set_major_locator(MaxNLocator(nbins=4))
    section.set_ylabel("离地高度 / m", color=height_color)
    section.tick_params(axis="y", colors=height_color, labelsize=8)
    for side in ("top", "left", "bottom"):
        section.spines[side].set_visible(False)
    section.spines["right"].set_color("#bdcbd6")

    # 组件端点取自模型的南北向截面，支架为定位示意。
    for row, opacity in ((0, .68), (1, .38)):
        (x0, z0), (x1, z1) = panel_segment(cfg, row)
        xm, zm = panel_post(cfg, row)
        section.plot([xm, xm], [0, zm], color=panel_color, lw=2.8, alpha=opacity * .65)
        section.plot([x0, x1], [z0, z1], color=panel_color, lw=5, alpha=opacity,
                     solid_capstyle="butt")
    section.axhline(ev.canopy, color=height_color, ls=(0, (3, 4)), lw=.9, alpha=.58)
    intervals = best["intervals"]
    left_limit, right_limit = intervals[0][1], intervals[-1][2]
    section.annotate("", xy=(right_limit, .88 * zmax), xytext=(left_limit, .88 * zmax),
                     arrowprops={"arrowstyle": "<->", "color": height_color, "lw": .9})
    section.text((left_limit + right_limit) / 2, .9 * zmax, f"排距 S = {cfg.span:.2f} m",
                 color=height_color, ha="center", va="bottom", fontsize=8)

    section.vlines([low[0], high[0]], 0, [low[1], high[1]],
                   color=height_color, linestyles=":", lw=.8, alpha=.55)
    for zone, start, end in intervals:
        ax.axvspan(start, end, color=COL[zone], alpha=.09, lw=0, zorder=0)
        if start > left_limit:
            ax.axvline(start, color=COL[zone], ls="--", lw=.8, alpha=.65)
        if (end - start) / cfg.span >= (.11 if len(zone) > 2 else .07):
            ax.text((start + end) / 2, .045, zone, color=COL[zone], ha="center", fontsize=8)
    ax.axvline(right_limit, color=height_color, ls=":", lw=.9, alpha=.6)
    ax.plot(best["distance"], best["point"], color="#185e8d", lw=2.05, zorder=4)
    north_margin = min(.35 * (high[0] - low[0]), .14 * cfg.span)
    ax.set_xlim(left_limit - .025 * cfg.span, cfg.span + north_margin)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=7))
    ax.set_title("南北向采光剖面", loc="left", fontsize=11)
    ax.set_title(f"水平虚线 · 测点 {ev.canopy:g} m", loc="right", fontsize=8, color=height_color)
    ax.set_xlabel("距组件低侧投影 / m（南 → 北）")
    ax.set_ylabel("代表日平均采光率")
    ax.spines[["top", "right"]].set_visible(False)

    _plot_zone_means(bars, best)
    bars.set_title("分区平均采光率", loc="left", fontsize=11)
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.grid(axis="y", alpha=.2)
    return fig


def run_optimization(req):
    req = inputs.validate(req)
    lat = float(req.get("lat", 34.70))
    lon = float(req.get("lon", 110.07))
    tilt = float(req.get("tilt", 17.0))
    width = float(req.get("width", 4.18))
    mlen = float(req.get("mlen", 2.28))
    canopy = float(req.get("canopy", 1.0))
    crop = req.get("crop", "冬小麦")
    weights = (float(req.get("w_energy", 0.4)), float(req.get("w_yield", 0.4)), float(req.get("w_cv", 0.2)))
    y_min = float(req.get("y_min", 60.0)) / 100.0
    levels = 5

    weather, meta = inputs.weather(req)
    ev = Evaluator(weather, crop_name=crop, tilt=tilt, module_width=width,
                   module_len=mlen, canopy_height=canopy, lat=lat, lon=lon, economic=req["economic"], crop_price=req["crop_price"], pv_options=req["pv_options"])
    y_ck = ev.crop.yield_target

    keys = ["tilt", "s", "h", "g"]
    defaults = {"tilt": (12.0, 30.0), "s": (8.0, 16.0), "h": (1.8, 4.5), "g": (0.0, 0.6)}
    bounds, fixed, active = [], [], []
    for i, k in enumerate(keys):
        if bool(req.get(k + "_fix", False)):
            v = float(req.get(k + "_val", sum(defaults[k]) / 2))
            bounds.append((v, v)); fixed.append(v)
        else:
            lo = float(req.get(k + "_lo", defaults[k][0]))
            hi = float(req.get(k + "_hi", defaults[k][1]))
            lo, hi = min(lo, hi), max(lo, hi)
            bounds.append((lo, hi)); fixed.append(None); active.append(i)

    if active:
        U = doe.design(active, levels=levels)
        X = doe.coded_to_real(U, bounds)
    else:
        U = np.zeros((1, 4))
        X = np.array([fixed], dtype=float)

    runs = []
    evaluated = []
    for row in X:
        r = ev.evaluate(row[0], row[1], row[2], row[3])
        evaluated.append(r)
        runs.append({"tilt": r["tilt"], "s": r["span"], "h": r["height"], "g": r["gap"], "gcr": r["gcr"],
                     "under": r["T_under"], "between": r["T_between"], "yield": r["yield"],
                     "yield_ck": r["yield_ck"], "yield_pct": 100.0 * r["yield"] / r["yield_ck"] if r["yield_ck"] > 0 else None,
                     "energy": r["energy"], "energy_unshaded": r["energy_unshaded"], "pv_shading_loss": r["pv_shading_loss"],
                     "pv_losses":r["pv_losses"],
                     "net": r["net"], "ler": r["ler"], "cv": r["cv"]})

    y_ck_sim = runs[0]["yield_ck"]
    has_ck = y_ck_sim > 0
    y_ref = y_ck_sim if has_ck else y_ck
    y_threshold = y_min * y_ck_sim
    yield_scale = 100 / y_ck_sim if has_ck else 1.
    yield_name = "相对产量" if has_ck else "产量"
    yield_label = "相对产量 / %" if has_ck else "产量 / (kg·hm$^{-2}$)"
    if active:
        m_net, r2_net = doe.fit(U, [r["net"] for r in runs], active)
        m_y, r2_y = doe.fit(U, [r["yield"] for r in runs], active)
        m_cv, r2_cv = doe.fit(U, [r["cv"] for r in runs], active)
        m_u, r2_u = doe.fit(U, [r["under"] for r in runs], active)
        models = {"net": m_net, "yield": m_y, "cv": m_cv, "under": m_u}
        n = {4: 21, 3: 25, 2: 41}.get(len(active), 61)
        opt = doe.grid_optimum(models, bounds, weights, y_ref, active, n=n, y_min_frac=y_min if has_ck else 0.)
        bt, bs, bh, bg = opt["best_real"]
    else:
        r2_net = r2_y = r2_cv = r2_u = 1.0
        bt, bs, bh, bg = fixed
        m_net = m_y = None
        opt = {"pareto_net": np.array([r["net"] for r in runs]),
               "pareto_y": np.array([r["yield"] for r in runs]),
               "feasible": True, "best": {"net": runs[0]["net"], "score": 1.0}}
    best_score = 1.0
    search_summary={"selection":"综合遮挡及失配估算值","evaluated":1,"local_evaluated":0}
    if active:
        Uall, net_fit, y_fit, cv_fit, _ = opt["grid"]
        nmin, nmax, cvmax = opt["range"]
        def actual_score(r):
            w = np.asarray(weights) / sum(weights)
            return float(w[0] * ((r["net"] - nmin) / max(nmax - nmin, 1e-9)) + w[1] * np.clip(r["yield"] / y_ref, 0, 1.2) + w[2] * (1 - np.clip(r["cv"] / max(cvmax, 1e-9), 0, 1)))
        raw_score = (weights[0] * (net_fit - nmin) / max(nmax - nmin, 1e-9) + weights[1] * np.clip(y_fit / y_ref, 0, 1.2) + weights[2] * (1 - np.clip(cv_fit / max(cvmax, 1e-9), 0, 1))) / sum(weights)
        feasible_grid = np.where(y_fit >= y_threshold)[0]
        pool = feasible_grid if len(feasible_grid) else np.arange(len(Uall))
        candidate_ids = set(pool[np.argsort(-raw_score[pool])[:15]].tolist())
        candidate_ids.add(int(np.argmax(y_fit)))
        candidate_ids.add(int(np.argmax(raw_score)))
        # 沿高产候选均匀选样，避免只复算单一局部邻域。
        high = np.argsort(-y_fit)[:min(500, len(Uall))]
        candidate_ids.update(high[np.linspace(0, len(high) - 1, min(8, len(high)), dtype=int)].tolist())
        rows = list(evaluated)
        rows.extend(ev.evaluate(*doe.coded_to_real(Uall[idx], bounds)[0]) for idx in sorted(candidate_ids))
        feasible_rows = [r for r in rows if r["yield"] >= y_threshold - 1e-8]
        chosen = max(feasible_rows or rows, key=actual_score)
        # 失配损失存在分段变化，在真实估算内核上作局部坐标搜索，不仅修正拟合最优点。
        def rank(r):return (r["yield"] >= y_threshold - 1e-8, actual_score(r))
        local_count=0
        for step_fraction in (.08,.03,.01):
            for i in active:
                origin=[chosen[k] for k in ("tilt","span","height","gap")]
                for direction in (-1,1):
                    point=list(origin)
                    point[i]=float(np.clip(origin[i]+direction*step_fraction*(bounds[i][1]-bounds[i][0]),*bounds[i]))
                    if abs(point[i]-origin[i])<1e-10:continue
                    candidate=ev.evaluate(*point);rows.append(candidate);local_count+=1
                    if rank(candidate)>rank(chosen):chosen=candidate
        search_summary.update(evaluated=len(rows),local_evaluated=local_count)
        bt, bs, bh, bg = [chosen[k] for k in ("tilt", "span", "height", "gap")]
        best_score = actual_score(chosen)
        max_verified_yield = max(r["yield"] for r in rows) * yield_scale if has_ck else None
    else:
        max_verified_yield = runs[0]["yield_pct"]
    best = ev.evaluate(bt, bs, bh, bg, detail=True)
    feasible_actual = y_min <= 0 or (has_ck and best["yield"] >= y_threshold)

    names = ["组件倾角 β / °", "阵列跨度 S / m", "安装高度 H / m", "横向间隙 G / m"]
    if len(active) >= 2:
        i, j = active[0], active[1]
        xs = np.linspace(*bounds[i], 42); ys = np.linspace(*bounds[j], 42)
        XX, YY = np.meshgrid(xs, ys)
        base = np.array([bt, bs, bh, bg])
        pts = np.repeat(base[None, :], XX.size, axis=0)
        pts[:, i] = XX.ravel(); pts[:, j] = YY.ravel()
        UU = doe.real_to_coded(pts, bounds)
        NE = doe.predict(m_net, UU, active).reshape(XX.shape) / 10000.0
        YL = doe.predict(m_y, UU, active).reshape(XX.shape) * yield_scale
        fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.1))
        c1 = axes[0].contourf(XX, YY, NE, levels=14, cmap="YlOrRd")
        fig.colorbar(c1, ax=axes[0], label="净收益 / (万元·hm$^{-2}$)")
        axes[0].set_title("净收益响应面", fontsize=10)
        c2 = axes[1].contourf(XX, YY, YL, levels=14, cmap="YlGn")
        fig.colorbar(c2, ax=axes[1], label=yield_label)
        axes[1].set_title(yield_name + "响应面", fontsize=10)
        for ax in axes:
            ax.set_xlabel(names[i]); ax.set_ylabel(names[j])
            ax.plot(base[i], base[j], "*", ms=13, color="#1f3b57", markeredgecolor="white")
        fig.tight_layout(); img1 = _png(fig)
    elif len(active) == 1:
        i = active[0]
        xs = np.linspace(*bounds[i], 60)
        pts = np.zeros((60, 4)); pts[:] = [bt, bs, bh, bg]; pts[:, i] = xs
        UU = doe.real_to_coded(pts, bounds)
        fig, ax = plt.subplots(figsize=(5.8, 3.0))
        ax2 = ax.twinx()
        ax.plot(xs, doe.predict(m_net, UU, active) / 10000.0, color="#b5722a", lw=2, label="净收益")
        ax2.plot(xs, doe.predict(m_y, UU, active) * yield_scale,
                 color="#5b8c4a", lw=2, ls="--", label=yield_name)
        ax.set_xlabel(names[i])
        ax.set_ylabel("净收益 / (万元·hm$^{-2}$)", color="#b5722a")
        ax2.set_ylabel(yield_label, color="#5b8c4a")
        ax.grid(alpha=0.25)
        fig.tight_layout(); img1 = _png(fig)
    else:
        fig, ax = plt.subplots(figsize=(5.6, 2.6))
        _plot_zone_means(ax, best)
        ax.set_ylabel("采光率")
        fig.tight_layout(); img1 = _png(fig)

    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    ax.scatter([r["net"] / 10000 for r in runs], [r["yield"] * yield_scale for r in runs], s=26,
               color="#9aa3ad", label="试验点")
    if active:
        ax.scatter(opt["pareto_net"] / 10000, opt["pareto_y"] * yield_scale, s=16, color="#c9a227", label="拟合网格近似非支配集")
    ax.scatter([best["net"] / 10000], [best["yield"] * yield_scale], s=120, marker="*",
               color="#1f3b57", edgecolor="white", label="推荐方案", zorder=5)
    ax.set_xlabel("净收益 / (万元·hm$^{-2}$)"); ax.set_ylabel(yield_label)
    ax.grid(alpha=0.25); ax.legend(fontsize=7, frameon=False)
    fig.tight_layout(); img2 = _png(fig)

    img3 = _png(_light_profile(ev, best))

    warnings = list(meta.get("warnings", []))
    meta["crop_scope"] = best["crop_scope"]
    meta["cost_year_fraction"] = best["cost_year_fraction"]
    if meta.get("calculation_basis") == "actual_period":
        if best["crop_scope"]["partial_segments"]:
            warnings.append("部分作物季未完整覆盖：产量和作物收益为已有日期内的生物量折算当量，未推算完整收获产量；起始水分沿用模型初始值。")
        if not best["crop_scope"]["observed_crop_days"]:
            warnings.append("所选时段未覆盖该作物生育日，作物产量当量为零。")
    if best["pv_geometry"]["truncated_directions"]:
        warnings.append("部分近水平射线达到邻排追踪上限，当前结果仍为估算，请核对极端密排条件。")
    labels = ["倾角", "跨度", "安装高度", "横向间隙"]
    for i in active:
        if min(abs([bt, bs, bh, bg][i] - x) for x in bounds[i]) <= .01 * (bounds[i][1] - bounds[i][0]):
            warnings.append(f"{labels[i]}达到搜索范围边界；仅代表当前范围内的推荐值，可调整范围后比较。")
    if not has_ck:
        warnings.append("该时段露地模拟产量为零，无法计算相对产量或核验相对产量约束。")
    elif not feasible_actual:
        warnings.append(f"本轮已复算方案未找到满足产量约束的解，不代表整个连续搜索域无解；已核验最高相对产量 {max_verified_yield:.1f}%，可将约束调至该值以下或调整跨度/间隙范围。")
    sens = diagnostics.sensitivity(ev, best, bounds, active)
    reports = {key: diagnostics.fit_report(U, [r[key] for r in runs], active, doe.real_to_coded([[bt, bs, bh, bg]], bounds)) for key in ("net", "yield", "cv", "under")} if active else {}
    for key, report in reports.items():
        actual = best["T_under"] if key == "under" else best[key]
        report["core_at_best"] = actual
        report["surrogate_error_at_best"] = report["surrogate_at_best"] - actual
        interval = report["prediction_interval_95"]
        if interval and not interval[0] <= actual <= interval[1]:
            warnings.append(f"{dict(net='净收益', yield_='产量', cv='均匀性', under='板下采光').get(key, '产量')}在推荐点的内核复算超出响应面近似区间，建议加密设计或缩小变量范围；推荐卡片仍使用内核实际值。")
    return {
        "ok": True, "version": VERSION, "created_at": datetime.now().isoformat(timespec="seconds"),
        "request": req, "crop_info": crop_metadata()[crop], "warnings": warnings, "sensitivity": sens, "diagnostics": reports,
        "max_verified_yield_pct": max_verified_yield,
        "search_summary":search_summary,
        "model_notes": ["光伏发电已计入排间直射、周期有限横向间隙、天空/地面视角遮挡及 Martinez 旁路分区失配估算；所有试验点与候选复算均使用折减后电量。", "接线参数为用户可改的初始假设；周期平地矩形排阵不包括实际电站端部、地形及完整电池 I-V 网络。地面反射源仍按均匀 GHI 估算。", "作物地面采光采用二维条带加间隙修正；大荔实测 DLI 存在板下高估，尚待验证。", "完整生育季用露地目标值校准基准产量。实际产量含高温、午间蒸发需求及强光折减。", "发电未计双面增益和年衰减。净收益采用直线年摊销，未使用折现率。"],
        "opt": {"tilt": best["tilt"], "s": best["span"], "h": best["height"], "g": best["gap"], "gcr": best["gcr"],
                "period_days": best["period_days"], "date_start": meta.get("date_start"), "date_end": meta.get("date_end"),
                "calculation_basis": meta.get("calculation_basis", "typical_year"), "yield_basis":best["crop_scope"]["yield_basis"],
                "cost_year_fraction":best["cost_year_fraction"],
                "under": best["T_under"], "rim": best["T_rim"], "between": best["T_between"],
                "rim_low": best["zones"]["低侧板沿"]["T"] if best["frac"]["低侧板沿"] else None,
                "rim_high": best["zones"]["高侧板沿"]["T"] if best["frac"]["高侧板沿"] else None,
                "zone_area_fraction": best["frac"], "zone_intervals_m": best["intervals"],
                "yield": best["yield"], "yield_ck": best["yield_ck"],
                "yield_pct": best["yield"] * yield_scale if has_ck else None,
                "energy": best["energy"], "ler": best["ler"],
                "energy_unshaded": best["energy_unshaded"], "pv_shading_loss": best["pv_shading_loss"],
                "pv_losses":best["pv_losses"],"energy_stages":best["energy_stages"],"pv_geometry":best["pv_geometry"],
                "net": best["net"], "cv": best["cv"], "cap": best["capacity"],
                "shadow": best.get("shadow_width", 0.0), "ref_pitch": best["ref_pitch"],
                "score": best_score, "feasible": bool(feasible_actual)},
        "runs": runs, "active": [keys[i] for i in active],
        "r2": {"net": r2_net, "yield": r2_y, "cv": r2_cv, "under": r2_u},
        "meta": meta,
        "plots": {"r1": img1, "r2": img2, "r3": img3},
    }


PAGE = Path(BASE, "AVOpt/static/index.html").read_text(encoding="utf-8")
PAGE = PAGE.replace("<!--CROP_CONFIG-->", "<script>window.__CROPS__=" + json.dumps(crop_metadata(), ensure_ascii=False) + ";</script>")



class LocalHTTPServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 可能允许两个监听器绑定同一端口，必须排他绑定。
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(port=8765, attempts=20):
    for offset in range(attempts):
        try:
            return LocalHTTPServer(("127.0.0.1", port + offset if port else 0), Handler)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 10048 and exc.errno not in (48, 98, 10048):
                raise
    raise RuntimeError("连续 20 个端口被占用，请指定其他起始端口")


def _run_job(job_id, req):
    try:
        with JOB_LOCK:
            JOBS[job_id]["state"] = "running"
        with PLOT_LOCK:
            try:
                result = run_optimization(req)
            finally:
                plt.close("all")
        # 提前检查序列化，避免后台完成后轮询接口才报错。
        json.dumps(result, ensure_ascii=False, default=float, allow_nan=False)
        with JOB_LOCK:
            JOBS[job_id].update(state="done", result=result, finished=time.monotonic())
    except Exception as exc:
        LOGGER.exception("background calculation failed")
        with JOB_LOCK:
            JOBS[job_id].update(state="failed", error=str(exc), finished=time.monotonic())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        LOGGER.info(fmt, *args)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif url.path == "/client.js":
            self._send(200, Path(BASE, "AVOpt/static/client.js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8")
        elif url.path == "/api/health":
            self._send(200, json.dumps({"ok": True, "version": VERSION}))
        elif url.path == "/api/jobs":
            job_id = urllib.parse.parse_qs(url.query).get("id", [""])[0]
            with JOB_LOCK:
                job = dict(JOBS[job_id]) if job_id in JOBS else None
            if job is None:
                self._send(404, json.dumps({"ok": False, "error": "计算任务不存在或服务已重启，请重新计算。"}, ensure_ascii=False))
            elif job["state"] == "failed":
                self._send(200, json.dumps({"ok": False, "error": job["error"]}, ensure_ascii=False))
            else:
                data = {"ok": True, "state": job["state"]}
                if job["state"] == "done":
                    data["result"] = job["result"]
                self._send(200, json.dumps(data, ensure_ascii=False, default=float, allow_nan=False))
        elif url.path == "/api/weather/years":
            self._send(200, json.dumps({"ok": True, **period.catalog()}, ensure_ascii=False))
        elif url.path == "/api/weather/export":
            q = urllib.parse.parse_qs(url.query)
            try:
                years = [int(y) for y in q.get("years", [""])[0].split(",")]
                data = open_meteo.export_csv(q.get("lat",[None])[0], q.get("lon",[None])[0], years, q.get("weather_source",["era5"])[0])
                self._send(200, data, "text/csv; charset=utf-8")
            except (ValueError,TypeError) as exc:
                self._send(400, json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False))
        elif url.path in ("/api/radiation/status", "/api/radiation/request"):
            q = urllib.parse.parse_qs(url.query)
            try:
                lat, lon = q.get("lat", [None])[0], q.get("lon", [None])[0]
                years = [int(y) for y in q.get("years", [""])[0].split(",")]
                if url.path.endswith("/status"):
                    mode = q.get("weather_source", ["station"])[0]
                    data = open_meteo.coverage(lat,lon,years,mode) if mode in open_meteo.MODES else era5_land.status(lat, lon, years)
                else:
                    era5_land.expected_dates(years)
                    data = {"ok": True, "dataset": era5_land.DATASET, "requests": [era5_land.cds_request(lat, lon, y) for y in years]}
                self._send(200, json.dumps(data, ensure_ascii=False))
            except (ValueError, TypeError) as exc:
                self._send(400, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        elif url.path == "/api/place":
            q = urllib.parse.parse_qs(url.query).get("q", [""])[0]
            try:
                self._send(200, json.dumps({"ok": True, "items": inputs.places(q)}, ensure_ascii=False))
            except Exception as exc:
                self._send(503, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        elif url.path == "/api/reverse-place":
            q = urllib.parse.parse_qs(url.query)
            try:
                item = geocoding.reverse(q.get("lat", [None])[0], q.get("lon", [None])[0])
                self._send(200, json.dumps({"ok": True, "item": item}, ensure_ascii=False))
            except ValueError as exc:
                self._send(400, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            except Exception as exc:
                self._send(503, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def do_POST(self):
        if self.path not in ("/api/run", "/api/jobs", "/api/export", "/api/radiation/import", "/api/weather/download"):
            self._send(404, json.dumps({"ok": False, "error": "not found"}))
            return
        started = time.perf_counter()
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not 0 < n <= 8_000_000:
                raise ValueError("请求体为空或超过 8 MB")
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            if self.path == "/api/jobs":
                inputs.validate(req)
                now = time.monotonic()
                with JOB_LOCK:
                    expired = [key for key, job in JOBS.items() if job.get("finished", now) < now - 3600]
                    for key in expired:
                        del JOBS[key]
                    if sum(job["state"] in ("queued", "running") for job in JOBS.values()) >= 3:
                        self._send(429, json.dumps({"ok": False, "error": "当前计算任务较多，请稍后重试。"}, ensure_ascii=False))
                        return
                    job_id = uuid.uuid4().hex
                    JOBS[job_id] = {"state": "queued"}
                    JOB_POOL.submit(_run_job, job_id, req)
                self._send(202, json.dumps({"ok": True, "id": job_id}))
                return
            if self.path == "/api/weather/download":
                if not isinstance(req,dict):
                    raise ValueError("请求应为参数对象")
                mode = req.get("weather_source","era5")
                df, provenance = open_meteo.daily(req.get("lat"),req.get("lon"),req.get("years"),mode)
                self._send(200,json.dumps({"ok":True,"days":len(df),"start":str(df.index.min().date()),"end":str(df.index.max().date()),
                                           "cache_fallback": any(item.get("cache_fallback") for item in provenance),
                                           **open_meteo.coverage(req.get("lat"),req.get("lon"),req.get("years"),mode)},ensure_ascii=False))
                return
            if self.path == "/api/radiation/import":
                if not isinstance(req, dict):
                    raise ValueError("导入请求格式无效")
                result = era5_land.import_csv(req.get("csv_text"), req.get("format"), req.get("lat"), req.get("lon"), req.get("filename", "local.csv"))
                self._send(200, json.dumps(result, ensure_ascii=False))
                return
            if self.path == "/api/export":
                from .exports import excel
                self._send(200, excel(req), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                return
            # Matplotlib 有全局状态；并发请求按顺序计算，避免图像相互污染。
            with PLOT_LOCK:
                try:
                    res = run_optimization(req)
                finally:
                    plt.close("all")
            LOGGER.info("run parameters=%s elapsed=%.3fs", json.dumps(res["request"], ensure_ascii=False), time.perf_counter() - started)
            self._send(200, json.dumps(res, ensure_ascii=False, default=float, allow_nan=False))
        except (ValueError, TypeError, KeyError) as exc:
            self._send(400, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        except Exception as exc:
            LOGGER.exception("request failed elapsed=%.3fs", time.perf_counter() - started)
            self._send(500, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))


def main(port=8765, open_browser=True):
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    if not LOGGER.handlers:
        handler = RotatingFileHandler(os.path.join(BASE, "logs", "avopt.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
    srv = create_server(port)
    url = "http://127.0.0.1:%d/" % srv.server_port
    Path(BASE, "运行地址.txt").write_text(url, encoding="utf-8")
    print("农光互补布局优化系统 V" + VERSION + " 已启动：", url, flush=True)
    LOGGER.info("started version=%s url=%s", VERSION, url)
    print("按 Ctrl+C 退出", flush=True)
    if os.environ.get("AVOPT_NO_BROWSER") == "1":
        open_browser = False
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def make_snapshot(path, req=None, title_suffix="（示例）"):
    """生成带计算结果的静态页面，便于离线查看或作为软著截图。"""
    data = run_optimization(req or {"weather_source": "era5", "years":[period.latest_date().year]})
    html = PAGE.replace('<script src="/client.js"></script>', "<script>" + Path(BASE, "AVOpt/static/client.js").read_text(encoding="utf-8") + "</script>")
    html = html.replace('计算推荐方案</button>', '离线报告 · 无法重新计算</button>')
    html = html.replace("</body>",
                        "<script>window.__STATIC__=true;fill(%s);render(%s);</script></body>" % (json.dumps(data["request"], ensure_ascii=False).replace("<", "\\u003c"), json.dumps(data, ensure_ascii=False, default=float).replace("<", "\\u003c")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path

