"""请求校验、地区确认和气象订正；拒绝静默替换站点。"""
import math
from datetime import date
from dataclasses import asdict
import numpy as np
import pandas as pd
from AVSim.config import EconConfig
from .engine import CROPS, load_weather
from . import weather_cn, era5_land, open_meteo, period
from .crop_library import CROP_META
from AVSim.pv_losses import DEFAULTS as PV_DEFAULTS

KEYS = ["tilt", "s", "h", "g"]
DEFAULTS = {"tilt": (12., 30.), "s": (8., 16.), "h": (1.8, 4.5), "g": (0., .6)}
# 本地项目已有坐标：大荔站点及西安历史辐射缓存，作为近似参考，仍允许用户核对。
COORDS = {"大荔县": (34.70, 110.07), "西安市": (34.34, 108.94)}

def number(req, key, default, lo=None, hi=None):
    try:
        value = float(req.get(key, default))
    except (ValueError, TypeError):
        raise ValueError(f"{key} 需填写数字") from None
    if not math.isfinite(value) or (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"{key} 超出有效范围（{lo}～{hi}）")
    return value

def validate(req):
    if not isinstance(req, dict):
        raise ValueError("请求应为参数对象")
    r = dict(req)
    r["weather_source"] = req.get("weather_source", "era5")
    r["radiation_source"] = "era5" if r["weather_source"] in open_meteo.MODES else "era5_land"
    r["lat"] = number(req, "lat", 34.70, -60, 65)
    r["lon"] = number(req, "lon", 110.07, -180, 180)
    r["width"] = number(req, "width", 4.18, .1, 30)
    r["mlen"] = number(req, "mlen", 2.28, .1, 30)
    r["canopy"] = number(req, "canopy", 1., 0, 10)
    if req.get("crop", "冬小麦") not in CROPS:
        raise ValueError("请选择冬小麦、冬油菜、夏玉米、大豆或花生")
    weights = [number(req, k, d, 0, 100) for k, d in [("w_energy", .4), ("w_yield", .4), ("w_cv", .2)]]
    if sum(weights) <= 0:
        raise ValueError("目标权重不能全为零")
    r["y_min"] = number(req, "y_min", 60., 0, 150)
    r["levels"] = 5  # 包括旧方案再次运行，统一迁移至五水平。
    ranges = {}
    for key, (low, high) in DEFAULTS.items():
        limit = {"tilt": (0, 80), "s": (.2, 100), "h": (.1, 20), "g": (0, 10)}[key]
        if not isinstance(req.get(key + "_fix", False), bool):
            raise ValueError("固定变量开关必须为布尔值")
        if req.get(key + "_fix", False):
            v = number(req, key + "_val", (low + high) / 2, *limit)
            r[key + "_val"] = v
            ranges[key] = (v, v)
        else:
            a = number(req, key + "_lo", low, *limit)
            b = number(req, key + "_hi", high, *limit)
            if a > b:
                raise ValueError(f"{key} 下限不能大于上限")
            r[key + "_lo"], r[key + "_hi"] = a, b
            if a == b:
                r[key + "_fix"], r[key + "_val"] = True, a
            ranges[key] = (a, b)
    if ranges["h"][0] <= r["canopy"]:
        raise ValueError("安装高度下限必须大于测点/冠层高度")
    if ranges["s"][0] <= r["width"] * math.cos(math.radians(ranges["tilt"][0])):
        raise ValueError("跨度下限必须大于最小倾角下的组件水平投影宽度")
    econ = req.get("economic", {})
    if not isinstance(econ, dict) or set(econ) - set(asdict(EconConfig())):
        raise ValueError("经济配置包含未知字段")
    r["economic"] = {k: number(econ, k, default, 0, 1e7) for k, default in asdict(EconConfig()).items()}
    name = req.get("crop", "冬小麦")
    legacy_price = econ.get("wheat_price", CROP_META[name]["price"]) if name == "冬小麦" else econ.get("rape_price", CROP_META[name]["price"]) if name == "冬油菜" else CROP_META[name]["price"]
    r["crop_price"] = number(req, "crop_price", legacy_price, 0, 10000)
    life = r["economic"]["pv_life"]
    if not (1 <= life <= 60 and life == int(life)):
        raise ValueError("光伏寿命需为 1～60 的整数年")
    options = req.get("pv_options",{})
    if not isinstance(options,dict) or set(options)-set(PV_DEFAULTS):
        raise ValueError("遮挡与接线估算参数包含未知字段")
    r["pv_options"] = dict(PV_DEFAULTS)
    for key,upper,label in (("modules_per_string",100,"同串计算单元数"),("bypass_blocks",24,"单元旁路分区数")):
        value=number(options,key,PV_DEFAULTS[key],1,upper)
        if value!=int(value):raise ValueError(label+"必须为整数")
        r["pv_options"][key]=int(value)
    axis=options.get("block_axis","slope")
    if axis not in ("slope","east_west"):raise ValueError("旁路分区方向无效")
    r["pv_options"]["block_axis"]=axis
    r["pv_options"]["row_offset"]=number(options,"row_offset",0,-30,30)
    # 当前收益算法采用直线年摊销，不假装使用折现率。
    return r

def places(query):
    items = weather_cn.search_place(query)
    for item in items:
        coords = COORDS.get(query.strip()) or COORDS.get(item["name"])
        if coords:
            item["lat"], item["lon"] = coords
            item["coordinate_note"] = "项目本地近似坐标，请核对实际地块位置"
    return items

def weather(req):
    lat, lon = req["lat"], req["lon"]
    place = str(req.get("place", "大荔县")).strip()
    source = req.get("weather_source", "era5")
    default_years = [period.latest_date().year] if source in open_meteo.MODES else [2022, 2023, 2024]
    dates = era5_land.expected_dates(req.get("years", default_years))
    years = sorted(set(dates.year))
    if source in open_meteo.MODES:
        w, meta = open_meteo.build(place, lat, lon, years, source)
    elif source == "builtin":
        if place not in ("大荔县", "大荔") or abs(lat - 34.70) > .05 or abs(lon - 110.07) > .05:
            raise ValueError("内置大荔气象仅用于大荔参考坐标附近；其他地区请选择在线站点")
        if any(y < 2020 or y > 2024 for y in years):
            raise ValueError("原内置大荔历史气象仅支持 2020—2024 年，请调整年份或选择全国再分析")
        ghi = era5_land.load(lat, lon, years)
        w = load_weather(tmean=None, precip=None, years=years)
        daily = ghi.loc[~((ghi.index.month == 2) & (ghi.index.day == 29))]
        w["ghi"] = daily.groupby(daily.index.strftime("%m-%d")).mean().reindex(pd.date_range("2021-01-01", periods=365).strftime("%m-%d")).to_numpy()
        meta = {"place": "大荔县", "years": list(map(int, years)), "source": "大荔历史气象 + ERA5-Land（辐射）", "meteorology_source": "历史 NASA POWER 温度、降水、风速和湿度；辐射已替换", "radiation": ghi.attrs.get("radiation", {}), "humidity_source": "历史 NASA POWER RH2M 格点数据", "warnings": []}
    elif source == "station":
        candidates = places(place)
        selected = next((p for p in candidates if str(p["areaCode"]) == str(req.get("area_code"))), None)
        if selected is None:
            raise ValueError("请先检索并选定具体气象行政区，再运行计算")
        if req.get("coordinates_confirmed") is not True:
            raise ValueError("请确认地块经纬度与所选气象行政区一致")
        era5_land.load(lat, lon, years)  # 缺辐射时立即提示，避免先等待站点联网。
        w, meta = weather_cn.build(selected, lat, lon, years)
        meta.update(source="yanshou100（温度/降水/风速）+ ERA5-Land（辐射）", humidity_source="按 Tmin/Tmean 估算，非实测湿度")
    else:
        raise ValueError("未知气象来源")
    w = {k: np.array(v, dtype=float, copy=True) for k, v in w.items()}
    before = {"tmean": float(w["tmean"].mean()), "precip": float(w["precip"].sum())}
    if req.get("bias_correct") is True:
        target_t = number(req, "target_tmean", 13.4, -40, 45)
        target_p = number(req, "target_precip", 514, 0, 10000)
        delta = target_t - before["tmean"]
        for k in ("tmean", "tmin", "tmax"):
            w[k] += delta
        if before["precip"] <= 0 and target_p > 0:
            raise ValueError("原序列全年无降水，无法按比例订正")
        w["precip"] *= target_p / max(before["precip"], 1e-9)
        meta["correction"] = {"temperature_shift": delta, "precip_scale": target_p / max(before["precip"], 1e-9), "reference": str(req.get("bias_reference", "用户填写的站点多年均值"))}
    if req.get("rh_override") not in (None, ""):
        rh = number(req, "rh_override", 65, 1, 100)
        w["rh"][:] = rh
        meta["humidity_source"] = f"用户指定固定日均湿度 {rh:g}%（所选时段统一值）"
    meta.update(lat=lat, lon=lon, before=before, tmean=float(w["tmean"].mean()), precip=float(w["precip"].sum()), ghi=float(w["ghi"].mean()), rh=float(w["rh"].mean()))
    return w, meta
