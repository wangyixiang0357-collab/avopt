"""周期矩形排阵光学估算 + Martinez 旁路分区电气失配估算。

有限的是每个 W×L 计算单元及横向间隙；不假装知道真实组件接线与电站端部。
"""
import math
from functools import lru_cache
import numpy as np
from . import solar, pv

DEFAULTS = {"modules_per_string": 20, "bypass_blocks": 3, "block_axis": "slope", "row_offset": 0.}
MAX_ROWS = 256

def _merge(intervals):
    merged = []
    for low, high in sorted(intervals):
        if high <= low:
            continue
        if merged and low <= merged[-1][1] + 1e-12:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged

def direction_shadow(east, north, up, tilt, width, pitch, length, gap, blocks=3, block_axis="slope", row_offset=0.):
    """单一入射方向的精确矩形阴影并集面积、触及旁路分区数、邻排截断标志。

    平行面射线交点的东西向位移与坡向位置无关；每个邻排投下矩形阴影，
    在坡向分段积分东西区间并集，避免把有限间隙仅按覆盖率折算。
    """
    beta = math.radians(tilt)
    normal_dot = up * math.cos(beta) - north * math.sin(beta)
    if normal_dot <= 1e-10 or abs(up) <= 1e-10 or abs(math.sin(beta)) <= 1e-12:
        return 0., 0, False
    depth = width * normal_dot / abs(up)
    if depth <= pitch:
        return 0., 0, False
    f1 = 1 - pitch / depth
    if gap <= 1e-10:
        count = math.ceil(blocks * f1 - 1e-12) if block_axis == "slope" else blocks
        return f1, max(0, min(blocks, count)), False
    period = length + gap
    shift = pitch * math.sin(beta) * east / normal_dot + row_offset
    needed = max(1, math.ceil(depth / pitch) - 1)
    count_rows = min(needed, MAX_ROWS)
    union = []
    area = 0.
    max_depth = 0.
    for k in range(1, count_rows + 1):
        f = max(0., 1 - k * pitch / depth)
        phase = (k * shift) % period
        pieces = [(0., min(length, length-phase)), (max(0., period-phase), length)]
        pieces = [(a, b) for a, b in pieces if b > a + 1e-12]
        if pieces:
            max_depth = max(max_depth, f)
            union = _merge(union + pieces)
        covered = sum(b-a for a, b in union) / length
        following = max(0., 1 - (k+1) * pitch / depth) if k < count_rows else 0.
        area += (f - following) * covered
        if covered >= 1 - 1e-12:
            area += following
            count_rows = needed  # 已覆盖全部东西区间，剩余邻排不会增加遮挡。
            break
    area = min(1., max(0., area))
    if area <= 1e-12:
        return 0., 0, needed > count_rows
    if block_axis == "slope":
        affected = math.ceil(blocks * max_depth - 1e-12)
    else:
        affected = sum(any(min((j+1)*length/blocks, b) > max(j*length/blocks, a) + 1e-12 for a, b in union) for j in range(blocks))
    return area, max(0, min(blocks, affected)), needed > count_rows

@lru_cache(maxsize=1024)
def view_factors(tilt, width, pitch, length, gap, row_offset=0., n_mu=12, n_az=32):
    """各向同性天空/均匀地面反射的可见比例，Gauss-Legendre 立体角积分。"""
    nodes, weights = np.polynomial.legendre.leggauss(n_mu)
    mus, weights = (nodes + 1)/2, weights/2
    beta = math.radians(tilt)
    result = []
    truncated = 0
    for hemisphere in (1., -1.):
        total, visible = 0., 0.
        for mu, weight in zip(mus, weights):
            horizontal = math.sqrt(1 - mu*mu)
            for az in (np.arange(n_az)+.5)*2*math.pi/n_az:
                east, north, up = horizontal*math.sin(az), horizontal*math.cos(az), hemisphere*mu
                incidence = up*math.cos(beta) - north*math.sin(beta)
                if incidence <= 0:
                    continue
                fraction, _, capped = direction_shadow(east,north,up,tilt,width,pitch,length,gap,row_offset=row_offset)
                w = weight * incidence
                total += w; visible += w*(1-fraction)
                truncated += int(capped)
        # 用解析的无遮挡视角定标，避免积分网格误差改变原始无遮挡辐射。
        open_factor = (1 + hemisphere*math.cos(beta))/2
        result.append(open_factor * visible/total if total > 0 else open_factor)
    return result[0], result[1], truncated

def martinez_beam_factor(shaded_fraction, shaded_blocks, total_blocks):
    """Martinez-Moreno (2010) 式6；已包含面积遮挡，不重复乘面积折减。"""
    fraction = np.asarray(shaded_fraction, float)
    affected = np.clip(np.ceil(shaded_blocks), 0, total_blocks)
    return np.clip((1-fraction)*(1-affected/(total_blocks+1.)), 0, 1)

def energy_yield(ghi, temperature, days, cfg, site, module_len, gap, options=None, step=1.):
    """分阶段电量：无遮挡 → 连续排直射 → 有限间隙 → 散射遮挡 → 失配。"""
    settings = {**DEFAULTS, **(options or {})}
    blocks, modules = settings["bypass_blocks"], settings["modules_per_string"]
    sun = solar.hourly_from_daily(ghi, days, site.lat, site.lon, site.tz, step=step)
    beta = math.radians(cfg.tilt)
    beam = sun["dni"] * np.clip(-sun["n"]*math.sin(beta) + sun["u"]*math.cos(beta), 0, None)
    sky_open = sun["dhi"] * (1+math.cos(beta))/2
    ground_open = sun["ghi"] * site.albedo * (1-math.cos(beta))/2
    base = beam + sky_open + ground_open
    continuous = pv.row_shaded_fraction(sun,cfg.tilt,cfg.width,cfg.span)
    fraction = np.zeros_like(beam)
    affected = np.zeros_like(beam)
    truncated = 0
    for index in zip(*np.nonzero(beam > 1e-9)):
        fraction[index], affected[index], capped = direction_shadow(float(sun["e"][index]),float(sun["n"][index]),float(sun["u"][index]),cfg.tilt,cfg.width,cfg.span,module_len,gap,blocks,settings["block_axis"],settings["row_offset"])
        truncated += int(capped)
    sky_vf, ground_vf, capped = view_factors(cfg.tilt,cfg.width,cfg.span,module_len,gap,settings["row_offset"])
    truncated += capped
    sky = sun["dhi"] * sky_vf
    ground = sun["ghi"] * site.albedo * ground_vf
    direct_only = base - beam*continuous
    finite = base - beam*fraction
    optical = beam*(1-fraction) + sky + ground
    # 当前周期排阵各同位置单元的遮挡相同；每串组件数决定总旁路分区数。
    electrical_beam = beam * martinez_beam_factor(fraction,affected*modules,blocks*modules)
    effective = electrical_beam + sky + ground
    tair = np.asarray(temperature,float)[:,None]
    def power(irradiance):
        tc = pv.cell_temperature(tair,irradiance,cfg.noct)
        pr = np.clip(1+cfg.temp_coef*(tc-25),0,None)*(1-cfg.system_loss)
        return irradiance/1000*pr
    final_power = power(optical) * np.divide(effective,optical,out=np.ones_like(optical),where=optical>1e-9)
    stages = {"unshaded":power(base),"continuous_direct":power(direct_only),"finite_direct":power(finite),"optical":power(optical),"final":final_power}
    daily = {key:value.sum(axis=1)*step for key,value in stages.items()}
    return {"daily_stages":daily,"daily_kwh_per_kwp":daily["final"],"daily_kwh_per_kwp_unshaded":daily["unshaded"],
            "shaded_fraction":fraction,"shaded_blocks":affected,"sky_view_factor":sky_vf,"ground_view_factor":ground_vf,
            "geometry_truncated_directions":truncated,"options":settings}
