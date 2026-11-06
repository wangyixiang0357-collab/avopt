"""参考蒸散与根区土壤水量平衡。"""
import numpy as np


def saturation_vapour_pressure(t):
    """饱和水汽压，kPa。"""
    return 0.6108 * np.exp(17.27 * t / (t + 237.3))


def et0_fao56(tmax, tmin, rh, ws, rs, doy, lat, elev):
    """FAO-56 Penman-Monteith 日参考蒸散，mm/d。

    tmax/tmin ℃，rh %，ws m/s（2 m），rs MJ/(m²·d)。
    """
    from .solar import extraterrestrial_radiation
    tmean = (tmax + tmin) / 2.0
    ra = extraterrestrial_radiation(doy, lat)
    rso = (0.75 + 2e-5 * elev) * ra
    rso = np.maximum(rso, 1e-6)
    es = (saturation_vapour_pressure(tmax) + saturation_vapour_pressure(tmin)) / 2.0
    ea = np.maximum(es * rh / 100.0, 1e-6)
    rns = 0.77 * rs
    rnl = 4.903e-9 * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2.0) * \
        (0.34 - 0.14 * np.sqrt(ea)) * (1.35 * np.clip(rs / rso, 0, 1.0) - 0.35)
    rn = rns - rnl
    delta = 4098 * 0.6108 * np.exp(17.27 * tmean / (tmean + 237.3)) / (tmean + 237.3) ** 2
    p = 101.3 * ((293 - 0.0065 * elev) / 293) ** 5.26
    gamma = 0.000665 * p
    u2 = np.maximum(ws, 0.1)
    num = 0.408 * delta * rn + gamma * (900.0 / (tmean + 273.0)) * u2 * (es - ea)
    den = delta + gamma * (1 + 0.34 * u2)
    return np.maximum(num / den, 0.0)


def kc_series(stage_frac, kc):
    """按生育进程插值作物系数。"""
    kc_ini, kc_mid, kc_end = kc
    f = np.clip(stage_frac, 0, 1)
    out = np.where(f < 0.25, kc_ini + (kc_mid - kc_ini) * f / 0.25,
                   np.where(f < 0.75, kc_mid,
                            kc_mid + (kc_end - kc_mid) * (f - 0.75) / 0.25))
    return out


def water_balance(precip, et0, kc, soil, rain_factor=1.0, irrigate=True):
    """逐日根区水量平衡。

    precip 为日降水 mm；rain_factor 为组件再分配系数（板下 <1，板沿 >1）。
    返回逐日 Ks、ETc、灌溉量、土壤有效水量。
    """
    n = len(precip)
    dr = 0.0
    ks = np.zeros(n)
    etc = np.zeros(n)
    irrig = np.zeros(n)
    storage = np.zeros(n)
    taw = soil.taw
    raw = soil.raw_frac * taw
    for i in range(n):
        p_eff = precip[i] * rain_factor
        if irrigate and dr > raw:
            irrig[i] = dr
            dr = 0.0
        ks[i] = 1.0 if dr <= raw else max(0.0, (taw - dr) / max(taw - raw, 1e-6))
        etc[i] = kc[i] * et0[i] * ks[i]
        dr = dr + etc[i] - p_eff - irrig[i]
        if dr < 0:          # 超过田间持水量的部分深层渗漏
            dr = 0.0
        dr = min(dr, taw)
        storage[i] = taw - dr
    return {"ks": ks, "etc": etc, "irrigation": irrig, "storage": storage}
