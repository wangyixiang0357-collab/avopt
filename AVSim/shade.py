"""阵列遮阴几何与分区采光率计算。

模型把固定式阵列视为沿东西方向无限延伸的条带，在南北—垂直二维平面内求解
射线与组件线段的交点。未显式计算有限阵列端部、支架和边框；与三维场景的差异
需结合实际几何和实测验证，不能由二维求交一致性推断。
"""
import numpy as np

UNDER_WIDTH_M = 2.50  # 柱中心南北合计宽度


def panel_segment(cfg, row_index=0):
    """返回第 row_index 排组件的下沿、上沿坐标（南北向 n，垂直向 z）。"""
    import math
    beta = math.radians(cfg.tilt)
    n0 = row_index * cfg.span
    return (n0, cfg.height), (n0 + cfg.width * math.cos(beta), cfg.height + cfg.width * math.sin(beta))


def panel_post(cfg, row_index=0):
    """示意柱位取组件截面的中点，返回柱顶的南北坐标和高度。"""
    low, high = panel_segment(cfg, row_index)
    return (low[0] + high[0]) / 2, (low[1] + high[1]) / 2


def column_zone_intervals(cfg):
    """一个柱间周期的四区截面，按南到北返回非空区段。"""
    center, _ = panel_post(cfg)
    half_span = cfg.span / 2
    under_half = min(UNDER_WIDTH_M / 2, half_span)
    # 板沿内端接板下，外端以边缘投影为中心镜像。相邻排冲突时在间隙中点截断。
    rim_limit = min(half_span, max(under_half, 2 * center - under_half))
    limits = [-half_span, -rim_limit, -under_half, under_half, rim_limit, half_span]
    zones = ["板间", "低侧板沿", "板下", "高侧板沿", "板间"]
    return [(zone, center + left, center + right)
            for zone, left, right in zip(zones, limits[:-1], limits[1:]) if right > left]


def hit_mask(n_grid, nd, ud, cfg, height=0.0):
    """判断测点沿太阳方向是否被组件遮挡。

    n_grid 测点南北坐标（m）；nd、ud 指向太阳的单位向量北向、天向分量；
    height 为测点离地高度（m），用于与不同高度的实测传感器对比。
    """
    import math
    n_grid = np.asarray(n_grid, dtype=float)
    shaded = np.zeros(n_grid.shape, dtype=bool)
    if ud <= 1e-6:
        return np.ones(n_grid.shape, dtype=bool)
    beta = math.radians(cfg.tilt)
    cb, sb = math.cos(beta), math.sin(beta)
    w = cfg.width
    for k in range(-cfg.rows, cfg.rows + 1):
        n0 = k * cfg.span
        denom = w * sb * nd / ud - w * cb
        if abs(denom) < 1e-9:
            continue
        h_eff = cfg.height - height
        s = (n0 - n_grid - h_eff * nd / ud) / denom
        t = (h_eff + s * w * sb) / ud
        shaded |= (s >= 0.0) & (s <= 1.0) & (t > 0.0)
    return shaded


def sky_view_factor(n_grid, cfg, n_az=48, n_el=14, height=0.0):
    """天空视角因子（各向同性天空），用于散射辐射的遮挡修正。"""
    n_grid = np.asarray(n_grid, dtype=float)
    el = np.linspace(np.pi / 180.0, np.pi / 2.0 * 0.999, n_el)
    az = np.linspace(0.0, 2.0 * np.pi, n_az, endpoint=False)
    weight = (np.cos(el) * np.sin(el))[:, None] * np.ones(n_az)[None, :]
    svf = np.zeros(n_grid.shape)
    wsum = weight.sum()
    for i, e in enumerate(el):
        for j, a in enumerate(az):
            nd = np.cos(e) * np.cos(a)
            ud = np.sin(e)
            blocked = hit_mask(n_grid, nd, ud, cfg, height=height)
            svf += weight[i, j] * (~blocked)
    return svf / wsum


def zone_masks(n_grid, cfg, rim_width=3.0):
    """按组件投影位置划分板下、板沿、板间三个区域。

    rim_width 为板沿过渡带宽度（m），默认 3.0 m，覆盖生长季阴影带的主体范围。
    """
    import math
    edge = cfg.width * math.cos(math.radians(cfg.tilt))
    n_grid = np.asarray(n_grid, dtype=float)
    under = n_grid <= edge
    rim = (n_grid > edge) & (n_grid <= edge + rim_width)
    between = n_grid > edge + rim_width
    return {"板下": under, "板沿": rim, "板间": between}


def instant_transmittance(n_grid, cfg, sun, svf, height=0.0):
    """瞬时地面辐照度透过率（逐点）。

    sun 为 solar_position 的输出（标量），svf 为天空视角因子。
    """
    nd, ud = float(sun["n"]), float(sun["u"])
    if ud <= 0.01:
        return np.ones_like(np.asarray(n_grid, dtype=float))
    ghi = float(sun["dhi"]) + float(sun["dni"]) * ud
    if ghi <= 0:
        return np.ones_like(np.asarray(n_grid, dtype=float))
    shaded = hit_mask(n_grid, nd, ud, cfg, height=height)
    beam = float(sun["dni"]) * ud * (~shaded)
    diff = float(sun["dhi"]) * svf
    return (beam + diff) / ghi


def daily_zone_light(n_grid, cfg, doy, ghi_daily, site, svf=None, height=0.0):
    """计算某日三区的平均透过率与日光积分。

    返回 dict：trans（各区透过率）、dli（各区日光积分，mol/(m²·d)）、point（逐点日透过率）。
    """
    from . import solar
    if svf is None:
        svf = sky_view_factor(n_grid, cfg, height=height)
    hours = solar.hourly_from_daily(float(np.asarray(ghi_daily).ravel()[0]), float(np.asarray(doy).ravel()[0]), site.lat, site.lon, site.tz)
    masks = zone_masks(n_grid, cfg)
    acc = np.zeros_like(n_grid, dtype=float)
    dli_point = np.zeros_like(n_grid, dtype=float)
    for i in range(len(hours["hour"])):
        sun = {k: (v[i] if hasattr(v, "__len__") else v) for k, v in hours.items()}
        if not sun["mask"]:
            continue
        sun_dn = {"n": float(sun["n"]), "u": float(sun["u"]),
                  "dni": float(sun["dni"]), "dhi": float(sun["dhi"])}
        tr = instant_transmittance(n_grid, cfg, sun_dn, svf, height=height)
        gnd = tr * (float(sun["dni"]) * float(sun["u"]) + float(sun["dhi"]))
        dli_point += 0.45 * gnd * 4.57 * 0.5 * 3600.0 / 1e6  # mol/(m²·d)
        acc += tr * (float(sun["dni"]) * float(sun["u"]) + float(sun["dhi"]))
    open_sum = sum(float(hours["dni"][i]) * float(hours["u"][i]) + float(hours["dhi"][i])
                   for i in range(len(hours["hour"])) if hours["mask"][i])
    point_trans = acc / max(open_sum, 1e-9) * (1.0 - getattr(cfg, 'structure_loss', 0.0))
    trans = {k: float(point_trans[m].mean()) if m.any() else 0.0 for k, m in masks.items()}
    dli = {k: float(dli_point[m].mean()) * (1.0 - getattr(cfg, 'structure_loss', 0.0)) if m.any() else 0.0 for k, m in masks.items()}
    return {"trans": trans, "dli": dli, "point": point_trans, "masks": masks, "svf": svf}
