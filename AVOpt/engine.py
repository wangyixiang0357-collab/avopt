"""单方案评估：给定 (跨度 S, 架高 H, 横向间隙 G) 计算光环境、产量与发电量。"""
import math
import os
import calendar
from datetime import date
from dataclasses import replace
from functools import cached_property
import numpy as np
import pandas as pd

from AVSim.config import ArrayConfig, CropConfig, SiteConfig, SoilConfig, EconConfig
from AVSim import shade, crop as crop_mod, photosynthesis, water, pv, econ, io_utils
from AVSim import pv_losses
from .crop_library import CROPS, CROP_META, PHOTOSYNTHESIS

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DEFAULT = os.path.join(BASE, "data", "nasa_power_dali_2015_2024.json")
ZONE_RAIN_FACTORS = {"低侧板沿": 1.40, "板下": .80, "高侧板沿": 1.40, "板间": 1.10}


def load_weather(path=None, tmean=13.4, precip=514.0, years=None):
    """读取旧模式历史气象；网页新模式另行读取 ERA5 实际日期序列。"""
    path = path or DATA_DEFAULT
    df = io_utils.load_power_json(path)
    if years is not None:
        df = df.loc[df.index.year.isin(years)].copy()
        if df.empty:
            raise ValueError("内置大荔历史气象仅覆盖 2015—2024 年")
    df = io_utils.bias_correct(df, tmean_target=tmean, precip_target=precip)
    return io_utils.typical_year(df)


class Evaluator:
    """包装一次计算的上下文（气象、作物、土壤、经济），避免重复读取。"""

    def __init__(self, weather, crop_name="冬小麦", tilt=30.0, module_width=4.18,
                 module_len=2.28, canopy_height=1.0, lat=34.70, lon=110.07, economic=None, crop_price=None, pv_options=None):
        self.weather = weather
        self.crop = CropConfig(**CROPS[crop_name])
        self.photo = PHOTOSYNTHESIS[crop_name]
        self.tilt = tilt
        self.width = module_width
        self.module_len = module_len
        self.canopy = canopy_height
        self.soil = SoilConfig()
        self.econ = EconConfig(**(economic or {}))
        self.crop_price = float(crop_price) if crop_price is not None else (self.econ.wheat_price if crop_name == "冬小麦" else self.econ.rape_price if crop_name == "冬油菜" else CROP_META[crop_name]["price"])
        self.site = SiteConfig(lat=lat, lon=lon)
        self.pv_options = {**pv_losses.DEFAULTS, **(pv_options or {})}
        self.period_dates = ([date.fromordinal(int(v)) for v in weather["date_ordinal"]]
                             if "date_ordinal" in weather else None)
        self.solar_doys = (np.array([d.timetuple().tm_yday for d in self.period_dates])
                           if self.period_dates else np.arange(1, 366))
        self.period_years = (sum(1/(366 if calendar.isleap(d.year) else 365) for d in self.period_dates)
                             if self.period_dates else 1.)

    def gcr(self, span, gap, tilt=None):
        """考虑横向间隙的垂直投影覆盖率。"""
        t = self.tilt if tilt is None else tilt
        proj = self.width * math.cos(math.radians(t))
        return proj / span * self.module_len / (self.module_len + gap)

    def gap_factor(self, gap):
        """横向间隙带来的散射透过增益（一阶近似）。"""
        return 0.6 * gap / (self.module_len + gap)

    def light(self, span, height, gap, tilt=None, sample_days=None, n_grid=121):
        """按太阳高度角投影的阴影带计算分区采光率（解析法，与射线追踪等价）。"""
        from AVSim import shadow, solar
        t = self.tilt if tilt is None else tilt
        cfg = ArrayConfig(tilt=t, height=height, span=span, width=self.width)
        if sample_days is None:
            sample_days = (sorted(set([1, len(self.period_dates)] + [i+1 for i,d in enumerate(self.period_dates) if d.day in (8,23)]))
                           if self.period_dates else io_utils.sample_days(2))
        intervals = shade.column_zone_intervals(cfg)
        cell_edges = np.linspace(intervals[0][1], intervals[-1][2], n_grid + 1)
        n = (cell_edges[:-1] + cell_edges[1:]) / 2
        zone_names = list(ZONE_RAIN_FACTORS)
        widths = np.zeros((len(zone_names), n_grid))
        masks = {zone: np.zeros(n_grid, dtype=bool) for zone in zone_names}
        for zone, left, right in intervals:
            overlap = np.minimum(cell_edges[1:], right) - np.maximum(cell_edges[:-1], left)
            widths[zone_names.index(zone)] += np.maximum(overlap, 0)
            masks[zone] |= (n >= left) & (n < right)
        zone_widths = widths.sum(axis=1)
        # 跨界网格按实际重叠宽度分配，极窄分区也有面积和采光统计。
        weights = np.divide(widths, zone_widths[:, None], out=np.zeros_like(widths),
                            where=zone_widths[:, None] > 0)
        svf = shade.sky_view_factor(n, cfg, n_az=28, n_el=8, height=self.canopy)
        width_max = 0.0
        point_all = []
        beam_samples = []
        for d in sample_days:
            i = int(d) - 1
            solar_day = float(self.solar_doys[i])
            hours = solar.hourly_from_daily(float(self.weather["ghi"][i]), solar_day,
                                            self.site.lat, self.site.lon, self.site.tz)
            pos = solar.solar_position(solar_day, hours["hour"], self.site.lat, self.site.lon)
            acc = np.zeros_like(n)
            beam_share = np.zeros((len(zone_names), len(hours["hour"])))
            open_sum = 0.0
            for k in range(len(hours["hour"])):
                if not hours["mask"][k]:
                    continue
                elev = float(pos["elev"][k])
                if elev <= 0.01:
                    continue
                az = float(pos["azim"][k])
                exp = shadow.exposure(n, span, height - self.canopy, self.width, t, elev, az)
                beam_share[:, k] = weights @ exp
                _, L = shadow.shadow_band(span, height - self.canopy, self.width, t, elev, az)
                width_max = max(width_max, L)
                beam = float(hours["dni"][k]) * float(hours["u"][k])
                diff = float(hours["dhi"][k])
                acc += beam * exp + diff * svf
                open_sum += beam + diff
            pt = acc / max(open_sum, 1e-9)
            point_all.append(pt)
            beam_samples.append(beam_share)
        k_gain = self.gap_factor(gap)
        samples = np.asarray(point_all)
        samples = np.clip(samples + (1 - samples) * k_gain, 0, 1)
        zone_samples = samples @ weights.T
        trans = {zone: zone_samples[:, i] for i, zone in enumerate(zone_names)}
        solar_days = self.solar_doys[np.asarray(sample_days,dtype=int)-1]
        season = ((solar_days >= self.crop.sow_doy) | (solar_days <= self.crop.harvest_doy)) if self.crop.sow_doy > self.crop.harvest_doy else ((solar_days >= self.crop.sow_doy) & (solar_days <= self.crop.harvest_doy))
        if not season.any():
            season[:] = True
        return {"trans_sample": trans, "point": samples[season].mean(axis=0), "sample_days": list(map(int, sample_days)),
                "shadow_width": float(width_max), "masks": masks, "distance": n,
                "intervals": intervals, "frac": dict(zip(zone_names, zone_widths / span)),
                "beam_samples": np.asarray(beam_samples), "sky_share": weights @ svf}

    def evaluate(self, tilt, span, height, gap, detail=False):
        tilt = float(tilt)
        span, height, gap = float(span), float(height), float(gap)
        if not all(math.isfinite(v) for v in (tilt, span, height, gap)):
            raise ValueError("设计变量必须为有限数值")
        if not (0 <= tilt <= 80 and span > self.width * math.cos(math.radians(tilt)) and height > self.canopy >= 0 and gap >= 0):
            raise ValueError("需满足倾角 0～80°、跨度大于组件水平投影宽度、安装高度大于测点高度、间隙非负")
        light = self.light(span, height, gap, tilt=tilt)
        days = light["sample_days"]
        zones, ck, crop_scope, trans_daily = self._crop_results(light, gap)
        frac = light["frac"]
        y_av = sum(zones[z]["yield"] * frac[z] for z in zones)
        rim_zones = ("低侧板沿", "高侧板沿")
        rim_fraction = sum(frac[z] for z in rim_zones)
        rim_trans = sum(zones[z]["T"] * frac[z] for z in rim_zones) / rim_fraction if rim_fraction else 0.

        # 发电
        gcr = self.gcr(span, gap, tilt=tilt)
        t = self.tilt if tilt is None else tilt
        cfg = ArrayConfig(tilt=t, height=height, span=span, width=self.width)
        weights = (_period_weights(days, len(self.period_dates)) if self.period_dates else _weights(days))
        cap = 10000.0 * gcr / math.cos(math.radians(tilt)) * cfg.module_eff
        idx = np.asarray(days, dtype=int) - 1
        radiation = np.asarray(self.weather["ghi"])[idx]
        temperature = np.asarray(self.weather["tmean"])[idx]
        solar_days = self.solar_doys[idx]
        electricity = pv_losses.energy_yield(radiation, temperature, solar_days, cfg, self.site, self.module_len, gap, self.pv_options)
        kwh_per_kwp = float(np.dot(electricity["daily_kwh_per_kwp"], weights))
        unshaded_specific = float(np.dot(electricity["daily_kwh_per_kwp_unshaded"], weights))
        e_ha = float(kwh_per_kwp * cap)
        pitch, gcr_ref = pv.min_row_pitch(cfg, self.site)
        # 参照排距不同，需独立复算遮挡损失，不能沿用实际方案的比发电量。
        reference = pv_losses.energy_yield(radiation, temperature, solar_days, replace(cfg, span=pitch), self.site, self.module_len, 0., self.pv_options)
        ref_specific = float(np.dot(reference["daily_kwh_per_kwp"], weights))
        e_ref = ref_specific * 10000.0 * gcr_ref / math.cos(math.radians(tilt)) * cfg.module_eff
        stages = {key:float(np.dot(values,weights)*cap) for key,values in electricity["daily_stages"].items()}
        baseline = max(stages["unshaded"],1e-9)
        losses = {"row_direct":(stages["unshaded"]-stages["continuous_direct"])/baseline,
                  "gap_recovery":(stages["finite_direct"]-stages["continuous_direct"])/baseline,
                  "diffuse":(stages["finite_direct"]-stages["optical"])/baseline,
                  "mismatch":(stages["optical"]-stages["final"])/baseline}

        ler = econ.land_equivalent_ratio(y_av, ck["yield_kg_ha"], e_ha, e_ref) if ck["yield_kg_ha"] > 0 else None
        price = self.crop_price
        # 支架高度每增加 0.5 m，单位造价上浮 5%（2.0 m 为基准）
        capex_mult = min(1.5, 1.0 + 0.05 * max(0.0, height - 2.0) / 0.5)
        inc = econ.annual_income(y_av, price, e_ha, self.econ.tariff, self.econ.pv_capex_per_w * capex_mult,
                                 self.econ.pv_life, self.econ.pv_om_per_w_year,
                                 self.econ.ag_cost_per_ha, cap)
        if self.period_dates:
            inc["net"] = inc["crop_income"] + inc["energy_income"] - self.period_years*(inc["pv_annuity"]+inc["pv_om"]+inc["ag_cost"])

        # 均匀性：板下与板间点透过率的变异系数
        cv = float(np.std(light["point"]) / max(np.mean(light["point"]), 1e-9))
        res = {"tilt": tilt, "span": span, "height": height, "gap": gap, "gcr": gcr,
               "T_under": zones["板下"]["T"], "T_rim": rim_trans, "T_between": zones["板间"]["T"],
               "yield": y_av, "yield_ck": float(ck["yield_kg_ha"]), "bio": sum(zones[z]["bio"] * frac[z] for z in zones),
               "energy": e_ha, "energy_ref": e_ref, "capacity": cap,
               "energy_unshaded": unshaded_specific * cap,
               "pv_shading_loss": 1 - kwh_per_kwp / unshaded_specific if unshaded_specific > 0 else 0.,
               "pv_losses":losses,"energy_stages":stages,
               "pv_geometry":{"sky_view_factor":electricity["sky_view_factor"],"ground_view_factor":electricity["ground_view_factor"],"truncated_directions":electricity["geometry_truncated_directions"]},
               "ler": ler, "net": inc["net"] - getattr(self.econ, "land_rent_per_ha", 0.0)*self.period_years, "cv": cv, "capex_mult": capex_mult,
               "crop_scope":crop_scope, "period_days":len(self.period_dates) if self.period_dates else 365, "cost_year_fraction":self.period_years,
               "ref_pitch": pitch, "ref_gcr": gcr_ref, "shadow_width": light["shadow_width"],
               "zones": zones, "frac": frac}
        if detail:
            res["trans_daily"] = trans_daily
            res["point"] = light["point"]
            res["distance"] = light["distance"]
            res["intervals"] = light["intervals"]
        return res

    @cached_property
    def _crop_atmosphere(self):
        return photosynthesis.atmosphere(self.weather, self.solar_doys, self.site)

    @cached_property
    def _crop_seasons(self):
        """一次求解露地对照与物候，所有候选布局共享同一基准。"""
        if not self.period_dates:
            dates = crop_mod.season_dates(self.crop.sow_doy, self.crop.harvest_doy, 2021)
            segments = [(np.array([d.timetuple().tm_yday-1 for d in dates]), None, True)]
        else:
            ref = pd.Timestamp("2021-01-01")
            sow_md = ref + pd.Timedelta(days=self.crop.sow_doy-1)
            harvest_md = ref + pd.Timedelta(days=self.crop.harvest_doy-1)
            segments = []
            for y in range(min(d.year for d in self.period_dates)-1, max(d.year for d in self.period_dates)+1):
                start = date(y, sow_md.month, sow_md.day)
                end = date(y+(self.crop.sow_doy > self.crop.harvest_doy), harvest_md.month, harvest_md.day)
                idx = np.array([i for i,d in enumerate(self.period_dates) if start <= d <= end], dtype=int)
                if not len(idx):
                    continue
                full = len(idx) == (end-start).days+1
                f_calendar = np.array([(self.period_dates[i]-start).days/max((end-start).days,1) for i in idx])
                # 所选年份不连续时，各连续段独立起算水量，不跨缺失年份延续状态。
                split = np.where(np.diff([self.period_dates[i].toordinal() for i in idx]) != 1)[0]+1
                for part in np.split(np.arange(len(idx)), split):
                    segments.append((idx[part], f_calendar[part], full))
        seasons = []
        for idx, f_calendar, full in segments:
            cp = self.crop
            ghi,tmean,tmax,tmin,rh,ws,prec = [self.weather[k][idx] for k in ("ghi","tmean","tmax","tmin","rh","ws","precip")]
            et0 = water.et0_fao56(tmax,tmin,rh,ws,ghi,self.solar_doys[idx],self.site.lat,self.site.elevation)
            gdd = crop_mod.growing_degree_days(tmean,cp.tbase,cp.tcap)
            f = crop_mod.phenology(np.cumsum(gdd),float(np.sum(gdd))) if full else f_calendar
            kc = water.kc_series(f,cp.kc)
            wb_ck = water.water_balance(prec,et0,kc,self.soil,rain_factor=1.)
            air = {key: values[idx] for key, values in self._crop_atmosphere.items()}
            potential, actual = photosynthesis.effective_par(
                cp, self.photo, air["beam"] + air["diffuse"], air, wb_ck["ks"], f
            )
            # 按基准同化量校准，实际露地和光伏区均保留新增胁迫的扣减。
            unit_yield = float(potential.sum() * cp.hi * 10)
            rue = cp.yield_target / unit_yield if full and unit_yield > 1e-9 else cp.rue
            seasons.append({"idx": idx, "stage": f, "kc": kc, "precip": prec, "et0": et0, "air": air,
                            "rue": rue, "full": full, "open_yield": float(actual.sum() * rue * cp.hi * 10)})
        return seasons

    def _crop_results(self, light, gap):
        air = self._crop_atmosphere
        open_light = (air["beam"] + air["diffuse"]).sum(axis=1)
        day_indices = np.arange(1, len(open_light) + 1)
        sample_days = light["sample_days"]
        gain = self.gap_factor(gap)
        zone_radiation = {}
        trans_daily = {}
        for i, zone in enumerate(ZONE_RAIN_FACTORS):
            if light["frac"][zone] <= 0:
                trans_daily[zone] = np.zeros_like(open_light)
                continue
            sample = light["beam_samples"][:, i]
            if self.period_dates:
                beam_share = np.column_stack([np.interp(day_indices, sample_days, col) for col in sample.T])
            else:
                beam_share = np.column_stack([io_utils.circular_interp(sample_days, col, day_indices)
                                              for col in sample.T])
            diffuse_share = light["sky_share"][i]
            rs = air["beam"] * (gain + (1 - gain) * beam_share)
            rs += air["diffuse"] * (gain + (1 - gain) * diffuse_share)
            zone_radiation[zone] = rs
            trans_daily[zone] = np.clip(np.divide(rs.sum(axis=1), open_light,
                                                 out=np.zeros_like(open_light), where=open_light > 0), 0, 1)

        zones = {z: {"yield": 0., "bio": 0., "T": 0.} for z in ZONE_RAIN_FACTORS}
        ck_total, n, partial, full_count = 0., 0, 0, 0
        for season in self._crop_seasons:
            idx = season["idx"]
            full = season["full"]
            ck_total += season["open_yield"]
            n += len(idx)
            full_count += int(full)
            partial += int(not full)
            for z, rs in zone_radiation.items():
                tr = trans_daily[z][idx]
                wb = water.water_balance(season["precip"], season["et0"], season["kc"],
                                         self.soil, rain_factor=ZONE_RAIN_FACTORS[z])
                _, actual = photosynthesis.effective_par(
                    self.crop, self.photo, rs[idx], season["air"], wb["ks"], season["stage"],
                    shade_factor=1 - float(tr.mean())
                )
                biomass = float(actual.sum() * season["rue"] * 10)
                zones[z]["yield"] += biomass * self.crop.hi
                zones[z]["bio"] += biomass
                zones[z]["T"] += float(np.sum(tr))
        for z in zones:
            zones[z]["T"] = zones[z]["T"]/n if n else float(np.mean(trans_daily[z]))
        return zones, {"yield_kg_ha":ck_total}, {"observed_crop_days":n,"complete_seasons":full_count,"partial_segments":partial,
                "yield_basis":"observed_period_equivalent" if partial or not n else "complete_seasons"}, trans_daily


def _weights(days):
    """代表日的年化权重（相邻代表日间隔）。"""
    d = np.asarray(sorted(days), dtype=float)
    nxt = np.roll(d, -1)
    gap = np.where(nxt > d, nxt - d, nxt + 365 - d)
    return gap


def _period_weights(days, n_days):
    """代表日仅分配实际观测日的权重，权重总和严格等于时段天数。"""
    idx = np.asarray(days,dtype=int)-1
    nearest = np.abs(np.arange(n_days)[:,None]-idx[None,:]).argmin(axis=1)
    return np.bincount(nearest,minlength=len(idx)).astype(float)
