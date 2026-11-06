"""倾斜面辐照、组件温度与发电量估算。"""
import math
import numpy as np
from . import solar


def poa_irradiance(sun, tilt):
    """倾斜面总辐照度，W/m²。

    sun 为逐时太阳与辐射字典（数组），tilt 为倾角（度）。
    """
    beta = np.radians(tilt)
    nd, ud = np.asarray(sun["n"]), np.asarray(sun["u"])
    cos_theta = nd * (-np.sin(beta)) + ud * np.cos(beta)
    cos_theta = np.clip(cos_theta, 0.0, None)
    poa = np.asarray(sun["dni"]) * cos_theta + \
        np.asarray(sun["dhi"]) * (1 + np.cos(beta)) / 2.0 + \
        np.asarray(sun["ghi"]) * 0.20 * (1 - np.cos(beta)) / 2.0
    return np.where(sun["mask"], poa, 0.0)


def cell_temperature(tair, poa, noct=45.0):
    """组件温度，℃。"""
    return tair + (noct - 20.0) / 800.0 * poa


def row_shaded_fraction(sun, tilt, width, pitch):
    """同倾角、同下沿高度、平地无限长东西向排阵的板面直射遮挡比例。

    对朝南板面，D=W(cosβ-sinβ*n/u)，f=max(0,1-S/D)。
    仅在太阳位于板面正面且 D>S 时遮挡；等高排阵的绝对架高相消。
    与 pvlib infinite-sheds 的直射遮挡几何一致；不包含电池串失配。
    """
    if not (math.isfinite(width) and math.isfinite(pitch) and width > 0 and pitch > 0 and 0 <= tilt <= 80):
        raise ValueError("排间遮挡要求正的组件坡面宽与排距，倾角 0～80°")
    n, u = np.broadcast_arrays(np.asarray(sun["n"], float), np.asarray(sun["u"], float))
    beta = math.radians(tilt)
    projection = math.cos(beta) - math.sin(beta) * np.divide(n, u, out=np.zeros_like(n), where=u > 1e-8)
    distance = width * projection
    fraction = 1 - np.divide(pitch, distance, out=np.ones_like(distance), where=distance > 1e-8)
    front = (u > 1e-8) & (u * math.cos(beta) - n * math.sin(beta) > 0)
    return np.where(front, np.clip(fraction, 0, 1), 0.)


def energy_yield(ghi_daily, tmean_daily, doy, cfg, site, step=1.0, row_shading=False):
    """单位面积年发电量。

    ghi_daily MJ/(m²·d)，tmean_daily ℃，step 为小时步长。
    row_shading=True 时逐时扣减板面排间直射遮挡；Web 优化内核显式启用。
    默认 False 保留既有批量脚本计算口径；不包含散射遮挡与电池串失配。
    """
    sun = solar.hourly_from_daily(ghi_daily, doy, site.lat, site.lon, site.tz, step=step)
    unshaded_poa = poa_irradiance(sun, cfg.tilt)
    beta = math.radians(cfg.tilt)
    beam = np.asarray(sun["dni"]) * np.clip(-sun["n"] * math.sin(beta) + sun["u"] * math.cos(beta), 0, None)
    fraction = row_shaded_fraction(sun, cfg.tilt, cfg.width, cfg.span) if row_shading else np.zeros_like(beam)
    poa = np.maximum(0., unshaded_poa - beam * fraction)
    tair = np.asarray(tmean_daily, dtype=float)[:, None]
    def daily_energy(irradiance):
        tc = cell_temperature(tair, irradiance, cfg.noct)
        pr = np.clip(1 + cfg.temp_coef * (tc - 25.0), 0, None) * (1 - cfg.system_loss)
        return np.sum(irradiance / 1000.0 * pr, axis=1) * step
    daily = daily_energy(poa)
    daily_unshaded = daily_energy(unshaded_poa)
    kwh_per_kwp = float(daily.sum())
    cap_per_ha = 10000.0 * cfg.gcr / math.cos(math.radians(cfg.tilt)) * cfg.module_eff  # 组件坡面面积，kWp/ha
    return {"kwh_per_kwp": kwh_per_kwp,
            "kwh_per_ha": kwh_per_kwp * cap_per_ha,
            "capacity_kwp_per_ha": cap_per_ha,
            "poa": poa, "row_shaded_fraction": fraction,
            "daily_kwh_per_kwp": daily, "daily_kwh_per_kwp_unshaded": daily_unshaded,
            "kwh_per_kwp_unshaded": float(daily_unshaded.sum()),
            "row_shading_loss": float(1 - daily.sum() / daily_unshaded.sum()) if daily_unshaded.sum() > 0 else 0.}


def min_row_pitch(cfg, site, doy=355, hour=9.0):
    """按给定日、真太阳时高度角近似估计参考排距。

    默认冬至附近 9 时；不是全年无阴影阈值或工程规范验算。
    返回 (参考排距 m, 对应投影覆盖率)，参照方案仍须独立复算逐时遮挡。
    """
    beta = math.radians(cfg.tilt)
    h_top = cfg.height + cfg.width * math.sin(beta)
    decl = float(np.asarray(solar.declination(doy)).ravel()[0])
    lat_r = math.radians(site.lat)
    sin_h = (math.sin(lat_r) * math.sin(decl)
             + math.cos(lat_r) * math.cos(decl) * math.cos(math.radians(15.0 * (hour - 12.0))))
    elev = math.asin(max(min(sin_h, 1.0), -1.0))
    if elev <= 0:
        return cfg.span, cfg.gcr
    gap = (h_top - cfg.height) / math.tan(elev)
    pitch = cfg.width * math.cos(beta) + max(gap, 0.5)
    gcr = cfg.width * math.cos(beta) / pitch
    return pitch, gcr


def reference_energy(energy_av, cfg, site):
    """历史批量脚本的参照容量线性折算，保留原计算口径。

    若输入发电量含排间遮挡，该折算不能代表独立参考电站的逐时损失；
    当前 Web 内核改为按参照排距独立调用 energy_yield。
    """
    pitch, gcr_ref = min_row_pitch(cfg, site)
    return energy_av * gcr_ref / cfg.gcr, pitch, gcr_ref
