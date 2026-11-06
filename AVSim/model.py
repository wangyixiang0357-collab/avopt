"""方案计算主流程：光环境 → 作物 → 水分 → 发电 → 经济。"""
import numpy as np
import pandas as pd

from . import shade, crop as crop_mod, water, pv, econ, solar


def _zone_area_fraction(cfg):
    import math
    edge = cfg.width * math.cos(math.radians(cfg.tilt))
    f_under = edge / cfg.span
    f_rim = min(1.0, 1.0 / cfg.span)
    f_between = max(0.0, 1.0 - f_under - f_rim)
    return {"板下": f_under, "板沿": f_rim, "板间": f_between}


def annual_energy_sampled(scn, weather, days):
    """用代表日加权估算年发电量（kWh/ha）。

    参照发电量按同倾角、同架高、冬至日 9:00—15:00 不遮挡的最小间距折算，
    不再采用满铺假设。
    """
    w = np.asarray(days, dtype=float)
    order = np.argsort(w)
    w = w[order]
    nxt = np.roll(w, -1)
    gap = np.where(nxt > w, nxt - w, nxt + 365 - w)
    weights = gap
    total = 0.0
    for d, wt in zip(w, weights):
        i = int(d) - 1
        ghi = np.array([weather["ghi"][i]])
        tmean = np.array([weather["tmean"][i]])
        res = pv.energy_yield(ghi, tmean, np.array([d]), scn.array, scn.site, step=1.0)
        total += res["kwh_per_ha"] * wt
    ref, pitch, gcr_ref = pv.reference_energy(total, scn.array, scn.site)
    return {"energy_kwh_ha": total, "energy_ref_kwh_ha": ref,
            "capacity_kwp_ha": 10000.0 * scn.array.gcr * scn.array.module_eff,
            "ref_pitch_m": pitch, "ref_gcr": gcr_ref}


def run(scn, weather, sample_days=None, n_grid=121, svf_az=32, svf_el=10):
    """执行一次完整方案计算，返回结果字典。"""
    cfg = scn.array
    if sample_days is None:
        from .io_utils import sample_days as sd
        sample_days = sd(2)

    n = np.linspace(0.0, cfg.span, n_grid)
    svf = shade.sky_view_factor(n, cfg, n_az=svf_az, n_el=svf_el)

    trans_s = {k: [] for k in ("板下", "板沿", "板间")}
    dli_s = {k: [] for k in ("板下", "板沿", "板间")}
    point_store = {}
    for d in sample_days:
        i = int(d) - 1
        r = shade.daily_zone_light(n, cfg, np.array([d]), np.array([weather["ghi"][i]]),
                                   scn.site, svf=svf)
        for k in trans_s:
            trans_s[k].append(r["trans"][k])
            dli_s[k].append(r["dli"][k])
        point_store[int(d)] = r["point"]

    trans_daily = {k: np.interp(np.arange(1, 366), sample_days, trans_s[k])
                   for k in trans_s}
    # 端点平滑（周期）
    for k in trans_daily:
        trans_daily[k][0] = trans_daily[k][-1] = 0.5 * (trans_daily[k][0] + trans_daily[k][-1])

    # ---- 作物生长季 ----
    dates = crop_mod.season_dates(scn.crop.sow_doy, scn.crop.harvest_doy, 2021)
    doy = np.array([d.timetuple().tm_yday for d in dates])
    ghi = weather["ghi"][doy - 1]
    tmean = weather["tmean"][doy - 1]
    tmax = weather["tmax"][doy - 1]
    tmin = weather["tmin"][doy - 1]
    rh = weather["rh"][doy - 1]
    ws = weather["ws"][doy - 1]
    prec = weather["precip"][doy - 1]
    et0 = water.et0_fao56(tmax, tmin, rh, ws, ghi, doy, scn.site.lat, scn.site.elevation)

    gdd = crop_mod.growing_degree_days(tmean, scn.crop.tbase, scn.crop.tcap)
    total_gdd = float(np.sum(gdd))
    f = crop_mod.phenology(np.cumsum(gdd), total_gdd)
    kc = water.kc_series(f, scn.crop.kc)

    # 露地对照：标定 RUE
    wb_ck = water.water_balance(prec, et0, kc, scn.soil, rain_factor=1.0)
    scn.crop.rue = crop_mod.calibrate_rue(scn.crop, ghi, tmean, wb_ck["ks"], f)
    ck = crop_mod.simulate(scn.crop, ghi, tmean, wb_ck["ks"], f, shade_factor=0.0)

    rain_factor = {"板下": 0.80, "板沿": 1.50, "板间": 1.00}
    zones = {}
    for z in ("板下", "板沿", "板间"):
        tr = trans_daily[z][doy - 1]
        rs = ghi * tr
        wb = water.water_balance(prec, et0, kc, scn.soil, rain_factor=rain_factor[z])
        sim = crop_mod.simulate(scn.crop, rs, tmean, wb["ks"], f,
                                shade_factor=1.0 - float(np.mean(tr)))
        zones[z] = {
            "transmittance": float(np.mean(tr)),
            "dli_mol": float(np.mean(np.array(dli_s[z]))),
            "yield_kg_ha": sim["yield_kg_ha"],
            "biomass_kg_ha": float(sim["bio_g_m2"][-1] * 10.0),
            "etc_mm": float(np.sum(wb["etc"])),
            "irrigation_mm": float(np.sum(wb["irrigation"])),
            "lai_max": float(np.max(sim["lai"])),
        }

    frac = _zone_area_fraction(cfg)
    y_av = sum(zones[z]["yield_kg_ha"] * frac[z] for z in zones)
    bio_av = sum(zones[z]["biomass_kg_ha"] * frac[z] for z in zones)
    et_av = sum(zones[z]["etc_mm"] * frac[z] for z in zones)
    irr_av = sum(zones[z]["irrigation_mm"] * frac[z] for z in zones)

    en = annual_energy_sampled(scn, weather, sample_days)
    ler = econ.land_equivalent_ratio(y_av, ck["yield_kg_ha"],
                                     en["energy_kwh_ha"], en["energy_ref_kwh_ha"])
    price = scn.econ.wheat_price if scn.crop.name == "冬小麦" else scn.econ.rape_price
    inc = econ.annual_income(y_av, price, en["energy_kwh_ha"], scn.econ.tariff,
                             scn.econ.pv_capex_per_w, scn.econ.pv_life,
                             scn.econ.pv_om_per_w_year, scn.econ.ag_cost_per_ha,
                             en["capacity_kwp_ha"])

    return {
        "scenario": scn.to_dict(),
        "layout": {"tilt": cfg.tilt, "height": cfg.height, "span": cfg.span,
                   "width": cfg.width, "gcr": cfg.gcr},
        "light": {"monthly_trans": {k: [float(v) for v in trans_s[k]] for k in trans_s},
                  "monthly_dli": {k: [float(v) for v in dli_s[k]] for k in dli_s},
                  "sample_days": list(map(int, sample_days)),
                  "annual_mean_trans": {k: float(np.mean(trans_daily[k])) for k in trans_daily}},
        "crop": {"name": scn.crop.name, "rue": scn.crop.rue,
                 "ck_yield": ck["yield_kg_ha"], "ck_biomass": float(ck["bio_g_m2"][-1] * 10),
                 "zones": zones, "area_fraction": frac,
                 "area_yield": y_av, "area_biomass": bio_av,
                 "etc_mm": et_av, "irrigation_mm": irr_av},
        "energy": en,
        "ler": ler,
        "income": inc,
    }
