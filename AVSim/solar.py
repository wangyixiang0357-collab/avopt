"""太阳位置、天文辐射与逐时辐射分解。

参考 NOAA Solar Calculator 的低阶算法与 Erbs 散射比模型，适用于 0.5 h 步长的
田间尺度计算，精度满足农光互补光环境评估（太阳高度角误差 < 0.5°）。
"""
import numpy as np

SOLAR_CONSTANT = 1367.0  # W/m²


def day_angle(doy):
    """日角，弧度。"""
    return 2.0 * np.pi * (np.asarray(doy, dtype=float) - 1.0) / 365.0


def declination(doy):
    """太阳赤纬，弧度。"""
    g = day_angle(doy)
    return (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g)
            - 0.006758 * np.cos(2 * g) + 0.000907 * np.sin(2 * g)
            - 0.002697 * np.cos(3 * g) + 0.001480 * np.sin(3 * g))


def equation_of_time(doy):
    """时差，分钟。"""
    g = day_angle(doy)
    return 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                     - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))


def solar_position(doy, hour, lat, lon, tz=8):
    """计算太阳位置。

    参数
        doy  日序，标量或数组
        hour 地方标准时，小时（可含小数）
    返回
        字典，含 elev（高度角，弧度）、azim（方位角，自北顺时针，弧度）、
        e、n、u（指向太阳的单位向量东、北、天分量）
    """
    doy = np.asarray(doy, dtype=float)
    hour = np.asarray(hour, dtype=float)
    lat_r = np.radians(lat)
    decl = declination(doy)
    eot = equation_of_time(doy)
    tst = hour * 60.0 + eot + 4.0 * lon - 60.0 * tz
    ha = np.radians(tst / 4.0 - 180.0)

    sin_elev = np.sin(lat_r) * np.sin(decl) + np.cos(lat_r) * np.cos(decl) * np.cos(ha)
    sin_elev = np.clip(sin_elev, -1.0, 1.0)
    elev = np.arcsin(sin_elev)

    e = -np.cos(decl) * np.sin(ha)
    n = np.sin(decl) * np.cos(lat_r) - np.cos(decl) * np.cos(ha) * np.sin(lat_r)
    u = sin_elev
    azim = np.arctan2(e, n) % (2 * np.pi)
    return {"elev": elev, "azim": azim, "e": e, "n": n, "u": u}


def extraterrestrial_radiation(doy, lat):
    """大气顶日辐射量，MJ/(m²·d)。"""
    lat_r = np.radians(lat)
    decl = declination(doy)
    ws = np.arccos(np.clip(-np.tan(lat_r) * np.tan(decl), -1.0, 1.0))
    dr = 1.0 + 0.033 * np.cos(2.0 * np.pi * np.asarray(doy, dtype=float) / 365.0)
    ra = (24.0 * 60.0 / np.pi) * 0.0820 * dr * (
        ws * np.sin(lat_r) * np.sin(decl) + np.cos(lat_r) * np.cos(decl) * np.sin(ws))
    return ra


def erbs_diffuse_fraction(kt):
    """Erbs 模型：散射比 kd = DHI/GHI 与晴空指数 kt 的关系。"""
    kt = np.clip(kt, 0.0, 1.0)
    kd = np.where(
        kt <= 0.22, 1.0 - 0.09 * kt,
        np.where(kt <= 0.80,
                 0.9511 - 0.1604 * kt + 4.388 * kt ** 2 - 16.638 * kt ** 3 + 12.336 * kt ** 4,
                 0.165))
    return np.clip(kd, 0.0, 1.0)


def hourly_from_daily(ghi_daily, doy, lat, lon, tz=8, step=0.5):
    """把日总辐射（MJ/(m²·d)）分解为逐时平均辐照度（W/m²）。

    按晴空太阳高度角加权分配日总量，再用 Erbs 模型拆分直射与散射。
    标量输入返回长度 24/step 的一维数组；数组输入返回 (n, 24/step) 二维数组。
    """
    scalar = np.ndim(doy) == 0
    doy_arr = np.atleast_1d(np.asarray(doy, dtype=float))
    ghi_arr = np.atleast_1d(np.asarray(ghi_daily, dtype=float))
    hours = np.arange(step / 2.0, 24.0, step)
    dd = np.repeat(doy_arr[:, None], len(hours), axis=1)
    hh = np.repeat(hours[None, :], len(doy_arr), axis=0)
    pos = solar_position(dd, hh, lat, lon, tz)
    sin_e = np.clip(pos["u"], 0.0, None)
    w = sin_e / np.maximum(sin_e.sum(axis=1, keepdims=True), 1e-9)
    ghi = w * ghi_arr[:, None] * 1e6 / (step * 3600.0)
    clear = sin_e > 0.01
    i0 = SOLAR_CONSTANT * (1.0 + 0.033 * np.cos(2 * np.pi * dd / 365.0)) * sin_e
    kt = np.where(clear, ghi / np.maximum(i0, 1e-6), 0.0)
    kd = erbs_diffuse_fraction(kt)
    dhi = np.where(clear, ghi * kd, 0.0)
    dni = np.where(clear, (ghi - dhi) / np.maximum(sin_e, 1e-6), 0.0)
    out = {"hour": hh, "ghi": ghi, "dni": dni, "dhi": dhi, "elev": pos["elev"],
           "e": pos["e"], "n": pos["n"], "u": pos["u"], "mask": clear}
    if scalar:
        out = {k: v[0] for k, v in out.items()}
    return out


def daylight_hours(doy, lat):
    """可照时数，h。"""
    lat_r = np.radians(lat)
    decl = declination(doy)
    ws = np.arccos(np.clip(-np.tan(lat_r) * np.tan(decl), -1.0, 1.0))
    return 24.0 * ws / np.pi
