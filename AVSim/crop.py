"""作物物候、冠层截光与产量形成。

生物量按辐射利用效率（RUE）法计算：日增量 = RUE × 冠层截获 PAR × 温度修正
× 水分修正，累计地上部生物量乘收获指数得到经济产量。模型参数可依据当地
品种区试产量反算 RUE，使露地对照与实测一致。
"""
import numpy as np
from datetime import date, timedelta


def season_dates(sow_doy, harvest_doy, year):
    """生成跨年生长季的日期序列。"""
    d0 = date(year, 1, 1) + timedelta(days=sow_doy - 1)
    end_year = year + 1 if harvest_doy < sow_doy else year
    d1 = date(end_year, 1, 1) + timedelta(days=harvest_doy - 1)
    days = (d1 - d0).days + 1
    return [d0 + timedelta(days=i) for i in range(days)]


def growing_degree_days(tmean, tbase, tcap):
    """有效积温，℃·d。"""
    return np.clip(np.asarray(tmean, dtype=float) - tbase, 0.0, tcap - tbase)


def phenology(gdd_cum, total_gdd):
    """生育进程 0—1。"""
    return np.clip(gdd_cum / max(total_gdd, 1e-6), 0.0, 1.0)


def lai_curve(f, lai_max):
    """叶面积指数随生育进程变化：前期上升、中期稳定、后期衰减。"""
    f = np.asarray(f, dtype=float)
    rise = 0.30 + (lai_max - 0.30) * np.clip(f / 0.45, 0, 1) ** 1.3
    plateau = lai_max * np.ones_like(f)
    fall = lai_max * np.clip(1.0 - 0.75 * (f - 0.75) / 0.20, 0.15, 1.0)
    return np.where(f < 0.45, rise, np.where(f < 0.75, plateau, fall))


def temperature_factor(tmean, tbase, topt_hi=28.0, tmax_lim=35.0):
    """温度修正系数。"""
    t = np.asarray(tmean, dtype=float)
    low = np.clip((t - tbase) / 5.0, 0.0, 1.0)
    high = np.clip((tmax_lim - t) / 5.0, 0.0, 1.0)
    return low * high


def simulate(crop, rs_zone, tmean, ks, f, shade_factor=0.0):
    """逐日生物量累积。

    crop      作物参数
    rs_zone   分区日总辐射 MJ/(m²·d)
    tmean     日均温 ℃
    ks        水分胁迫系数 0—1
    f         生育进程 0—1
    shade_factor 遮阴强度（1-平均透过率），用于光能利用补偿
    """
    lai = lai_curve(f, crop.lai_max * (1 + crop.shade_lai_gain * shade_factor))
    par = 0.45 * np.asarray(rs_zone, dtype=float)
    fint = 1.0 - np.exp(-crop.k * lai)
    par_int = par * fint
    x = par_int / max(crop.par_k, 1e-6)
    fsat = (1.0 - np.exp(-x)) / np.maximum(x, 1e-6)      # 无量纲饱和修正，≤1
    gain = 1.0 + crop.shade_rue_gain * shade_factor
    limit = 32.0 if crop.name == "冬油菜" and crop.tmax_lim == 35.0 else crop.tmax_lim
    ft = temperature_factor(tmean, crop.tbase, tmax_lim=limit)
    daily = crop.rue * par_int * fsat * gain * ft * np.asarray(ks, dtype=float)
    bio = np.cumsum(daily)                      # g/m²
    return {"lai": lai, "par_int": par_int, "daily_bio": daily, "bio_g_m2": bio,
            "ft": ft,
            "yield_kg_ha": float(bio[-1] * crop.hi * 10.0)}


def calibrate_rue(crop, rs_zone, tmean, ks, f):
    """按露地目标产量反算 RUE，返回新的 RUE。"""
    unit = crop.rue
    crop.rue = 1.0
    try:
        res = simulate(crop, rs_zone, tmean, ks, f, shade_factor=0.0)
        bio_unit = res["bio_g_m2"][-1]
    finally:
        crop.rue = unit
    if bio_unit <= 0:
        return unit
    return crop.yield_target / (crop.hi * 10.0 * bio_unit)
