# -*- coding: utf-8 -*-
"""全国站点气象抓取（yanshou100）与 ERA5-Land 辐射，带本地缓存。

数据来源与口径
    温度、降水、风速：www.yanshou100.com 的 /weather/getWeatherData 接口，
        按省/市/县代码逐月获取逐日记录；
    相对湿度：接口未提供，按 Tmin/Tmean 由饱和水汽压比估算；
    水平面总辐射：ERA5-Land SSRD，由官方 CDS 工具或本地 CSV 导入。
"""
import json
import os
from pathlib import Path
import urllib.request
import urllib.parse
from datetime import date

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDR_PATH = os.path.join(BASE, "data", "tianqi-address.json")
CACHE = os.path.join(BASE, "data", "weather_cache")
os.makedirs(CACHE, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AVOpt/1.0"
SITE = "https://www.yanshou100.com"


def _post(path, payload, timeout=25):
    req = urllib.request.Request(SITE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA,
                                          "Referer": SITE + "/tianqi/tianqi.html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_address():
    """读取省—市—县三级地址库，返回扁平列表 [{name, province, city, area, codes}]。"""
    path = ADDR_PATH
    if not os.path.exists(path):
        raw = json.loads(_get(SITE + "/tianqi/json/tianqi-address.json").decode("utf-8"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
    else:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    flat = []
    for p in raw:
        for c in p.get("childs", []):
            for a in c.get("childs", []):
                flat.append({"name": a["name"], "province": p["name"], "city": c["name"],
                             "provinceCode": p["code"], "cityCode": c["code"], "areaCode": a["code"]})
    return flat


def search_place(keyword):
    """按名称检索行政区，返回匹配列表（完全匹配优先）。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    flat = load_address()
    exact = [x for x in flat if x["name"] == kw]
    if exact:
        return exact[:8]
    part = [x for x in flat if kw in x["name"] or kw in x["city"] or kw in x["province"]]
    return part[:8]


def _safe_month(place, year, month):
    try:
        return fetch_month(place, year, month)
    except Exception:
        return pd.DataFrame()


def fetch_month(place, year, month):
    key = os.path.join(CACHE, "ys_%s_%d%02d.json" % (place["areaCode"], year, month))
    if os.path.exists(key):
        try:
            return pd.DataFrame(json.loads(Path(key).read_text(encoding="utf-8")))
        except Exception:
            pass
    data = _post("/weather/getWeatherData", {"city": place["name"], "year": int(year),
                                             "month": int(month),
                                             "provinceCode": place["provinceCode"],
                                             "cityCode": place["cityCode"],
                                             "areaCode": place["areaCode"]})
    if not data or data.get("code") != 200:
        return pd.DataFrame()
    recs = data.get("data", {}).get("list", [])
    df = pd.DataFrame(recs)
    if len(df):
        Path(key).write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    return df


def fetch_daily(place, years, workers=8):
    """并发抓取各月数据，单站点 3 年约 8～12 s（首次），命中缓存后 <1 s。"""
    from concurrent.futures import ThreadPoolExecutor
    jobs = [(y, m) for y in years for m in range(1, 13)]
    frames = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d in ex.map(lambda ym: _safe_month(place, ym[0], ym[1]), jobs):
            if len(d):
                frames.append(d)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["recordDate"])
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    out = pd.DataFrame({
        "date": df["date"],
        "tmax": pd.to_numeric(df["maxTemperature"], errors="coerce"),
        "tmin": pd.to_numeric(df["minTemperature"], errors="coerce"),
        "tmean": pd.to_numeric(df["avgTemperature"], errors="coerce"),
        "precip": pd.to_numeric(df["precipitationAmount"], errors="coerce"),
        "ws": pd.to_numeric(df["avgWindSpeed"], errors="coerce"),
        "weather": df.get("weather", ""),
    })
    return out


def saturation_vp(t):
    return 0.6108 * np.exp(17.27 * np.asarray(t, dtype=float) / (np.asarray(t, dtype=float) + 237.3))


def estimate_rh(df):
    """用 Tmin/Tmean 估算日平均相对湿度（%）。"""
    ratio = saturation_vp(df["tmin"].to_numpy()) / saturation_vp(df["tmean"].to_numpy())
    return np.clip(ratio * 100.0, 25.0, 98.0)


def fetch_ghi(lat, lon, years):
    """ERA5-Land 逐日水平面总辐射，MJ/(m²·d)。"""
    from .era5_land import load
    return load(lat, lon, years)


def build(place, lat, lon, years=None):
    """组装典型年气象，返回 (weather dict, meta dict)。"""
    if years is None:
        y = date.today().year
        years = [y - 3, y - 2, y - 1]
    df = fetch_daily(place, years)
    if df.empty:
        raise RuntimeError("未获取到 %s 的逐日气象数据，请检查地区名称或网络" % place["name"])
    expected = pd.DatetimeIndex([d for y in years for d in pd.date_range(f"{y}-01-01", f"{y}-12-31")])
    df = df.set_index("date").reindex(expected).rename_axis("date").reset_index()
    required = ["tmean", "tmax", "tmin", "precip", "ws"]
    missing = int(df[required].isna().any(axis=1).sum())
    if missing:
        raise ValueError(f"所选年份有 {missing}/{len(df)} 天气象缺测（{missing / len(df):.1%}）；未将缺测降水填为零，请补齐数据或更换年份")
    invalid = ((df.tmin < -80) | (df.tmax > 65) | (df.tmin > df.tmean) | (df.tmean > df.tmax) | (df.precip < 0) | (df.precip > 1000) | (df.ws < 0) | (df.ws > 80))
    if invalid.any():
        raise ValueError(f"气象数据有 {int(invalid.sum())} 天超出有效范围或 Tmin/Tmean/Tmax 顺序错误")
    df["rh"] = estimate_rh(df)
    ghi = fetch_ghi(lat, lon, years)
    df["ghi"] = df["date"].map(ghi).astype(float)
    if df.ghi.isna().any() or not df.ghi.between(0, 60).all():
        raise ValueError("辐射数据存在缺测或极值（有效范围 0～60 MJ/m²/d），请检查来源")
    from AVSim.io_utils import typical_year
    weather = typical_year(df)
    zero = df.precip.eq(0)
    longest_zero = int(zero.groupby((~zero).cumsum()).sum().max())
    meta = {"place": place.get("name"), "province": place.get("province"),
            "city": place.get("city"), "areaCode": place.get("areaCode"),
            "years": list(map(int, years)), "days": int(len(df)), "radiation": ghi.attrs.get("radiation", {}),
            "tmean": float(df["tmean"].mean()), "precip": float(df.groupby(df.date.dt.year)["precip"].sum().mean()),
            "ghi": float(df["ghi"].mean()), "rh": float(df["rh"].mean()),
            "quality": {"missing_days": missing, "longest_zero_precip_days": longest_zero},
            "warnings": ([f"最长连续无降水 {longest_zero} 天，请核对是否符合当地情况"] if longest_zero >= 45 else [])}
    return weather, meta
