"""按太阳高度角与组件投影计算地面阴影带。

对沿东西向连续布置的固定式阵列，阴影在南北方向平移，形成宽度随太阳高度角
变化的条带。给定组件下沿高 H、坡面宽 W、倾角 β，太阳高度角 h 与方位角 A，
地面阴影带的南北起止为

    n1 = 0 − H·cot(h)·cos(A)
    n2 = W·cosβ − (H + W·sinβ)·cot(h)·cos(A)

条带长度 L = |n2 − n1|，当 L ≥ 阵列间距 S 时地面全遮阴。该式与二维射线追踪
在无限长条带假设下等价，用于替代逐点求交，计算量降低一个量级。
"""
import math
import numpy as np


def shadow_band(span, height, width, tilt, elev, azim):
    """返回 (起点 n1, 阴影带长度 L)，单位 m。"""
    if elev <= 0.01:
        return 0.0, float(span)
    beta = math.radians(tilt)
    cot = 1.0 / math.tan(elev)
    cos_a = math.cos(azim)
    n1 = -height * cot * cos_a
    n2 = width * math.cos(beta) - (height + width * math.sin(beta)) * cot * cos_a
    a, b = (n1, n2) if n1 <= n2 else (n2, n1)
    return a, min(b - a, float(span))


def exposure(n_grid, span, height, width, tilt, elev, azim):
    """地面各点的受光系数（0 遮阴，1 受光）。"""
    a, L = shadow_band(span, height, width, tilt, elev, azim)
    n = np.asarray(n_grid, dtype=float)
    if L >= span - 1e-9:
        return np.zeros_like(n)
    s = (n - a) % span
    return (s >= L).astype(float)


def shaded_area_ratio(span, height, width, tilt, elev, azim):
    """地面遮阴面积占农地面积的比例。"""
    _, L = shadow_band(span, height, width, tilt, elev, azim)
    return float(min(L / span, 1.0))


def band_widths(span, height, width, tilt, site_lat, doy, hours=range(6, 19)):
    """给定时段内的阴影带宽度序列，供报告使用。"""
    from AVSim import solar
    out = []
    for h in hours:
        p = solar.solar_position(float(doy), float(h), site_lat, 110.0, 8)
        elev = float(np.asarray(p["elev"]).ravel()[0])
        azim = float(np.asarray(p["azim"]).ravel()[0])
        if elev > 0.01:
            out.append((h, shadow_band(span, height, width, tilt, elev, azim)[1]))
    return out
