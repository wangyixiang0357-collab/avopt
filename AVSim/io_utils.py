"""气象数据读取、典型年构建与结果导出。"""
import json
import numpy as np
import pandas as pd


def load_power_json(path):
    """读取 NASA POWER 逐日 JSON，返回带日期的 DataFrame。

    POWER 农业社区返回的辐照单位为 MJ/(m²·d)，直接使用。
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    p = raw["properties"]["parameter"]
    df = pd.DataFrame({
        "tmean": pd.Series(p["T2M"]),
        "tmax": pd.Series(p["T2M_MAX"]),
        "tmin": pd.Series(p["T2M_MIN"]),
        "precip": pd.Series(p["PRECTOTCORR"]),
        "ghi": pd.Series(p["ALLSKY_SFC_SW_DWN"]),
        "rh": pd.Series(p["RH2M"]),
        "ws": pd.Series(p["WS2M"]),
    })
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.replace(-999.0, np.nan).interpolate()
    df["doy"] = df.index.dayofyear
    return df.sort_index()


def bias_correct(df, tmean_target=None, precip_target=None, ghi_target=None):
    """按气象站多年均值订正再分析序列（保持逐日时间结构，仅调整水平）。"""
    d = df.copy()
    if tmean_target is not None:
        d["tmean"] += tmean_target - d["tmean"].mean()
        d["tmax"] += tmean_target - df["tmean"].mean()
        d["tmin"] += tmean_target - df["tmean"].mean()
    if precip_target is not None:
        scale = precip_target / max(d["precip"].sum() / (len(d) / 365.25), 1e-6)
        d["precip"] *= scale
    if ghi_target is not None:
        d["ghi"] *= ghi_target / max(d["ghi"].mean(), 1e-6)
    return d


def typical_year(df):
    """按日序平均构建典型年，返回长度 365 的字典。"""
    d = df.copy()
    # 用非闰年的月日映射，避免闰年 3 月以后错位及 12 月 30/31 日合并。
    dates = pd.DatetimeIndex(d["date"] if "date" in d else d.index)
    keep = ~((dates.month == 2) & (dates.day == 29))
    d = d.loc[keep].copy()
    dates = dates[keep]
    d["doy"] = pd.to_datetime("2021-" + dates.strftime("%m-%d")).dayofyear.to_numpy()
    g = d.groupby("doy").mean(numeric_only=True)
    g = g.reindex(range(1, 366))
    if g.isna().any().any():
        raise ValueError("典型年存在缺测日，请先补齐并核对气象数据")
    return {k: g[k].to_numpy() for k in ("tmean", "tmax", "tmin", "precip", "ghi", "rh", "ws")}


def sample_days(per_month=2):
    """选取每月代表日（8 日、23 日），用于光环境抽样计算。"""
    days = []
    for m in range(1, 13):
        base = pd.Timestamp(year=2021, month=m, day=1)
        for k in range(per_month):
            d = base + pd.Timedelta(days=int(8 + k * 15) - 1)
            days.append(d.dayofyear)
    return sorted(set(days))


def circular_interp(doy_src, val_src, doy_tgt):
    """按日序做周期插值（1 与 365 首尾相接）。"""
    x = np.asarray(doy_src, dtype=float)
    y = np.asarray(val_src, dtype=float)
    xt = np.asarray(doy_tgt, dtype=float)
    x_ext = np.concatenate([x - 365.0, x, x + 365.0])
    y_ext = np.concatenate([y, y, y])
    order = np.argsort(x_ext)
    return np.interp(xt, x_ext[order], y_ext[order])


def export_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=float)
