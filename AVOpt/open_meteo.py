"""无密钥历史再分析，全部气象变量固定取 ERA5。

按坐标下载与缓存，保留各来源原始 JSON、单位、请求地址和实际日期；不混用预报。
"""
import hashlib
from http.client import IncompleteRead
import json
import logging
import math
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
from . import period

BASE = Path(__file__).resolve().parents[1]
CACHE = BASE / "data" / "open_meteo"
ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
MODES = ("era5",)
LOCK = threading.RLock()
LOGGER = logging.getLogger("AVOpt")
VARIABLES = {"tmean": "temperature_2m_mean", "tmax": "temperature_2m_max",
             "tmin": "temperature_2m_min", "rh": "relative_humidity_2m_mean",
             "precip": "precipitation_sum", "ghi": "shortwave_radiation_sum", "ws": "wind_speed_10m_mean"}
UNITS = {"tmean": "°C", "tmax": "°C", "tmin": "°C", "rh": "%", "precip": "mm", "ghi": "MJ/m²", "ws": "m/s"}
WIND_10M_TO_2M = 4.87 / math.log(67.8*10 - 5.42)  # FAO-56 Eq.47


def coordinates(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ValueError("请填写有效经纬度") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -60 <= lat <= 65 and -180 <= lon < 180):
        raise ValueError("经纬度超出模型范围")
    return round(lat, 5), round(lon, 5)


def sources(mode):
    if mode not in MODES:
        raise ValueError("未知历史再分析来源")
    return {"era5": list(VARIABLES)}


def _path(lat, lon, model, year, fields):
    lat, lon = coordinates(lat, lon)
    signature = hashlib.sha256(",".join(fields).encode()).hexdigest()[:8]
    return CACHE / f"{model}_{lat:.5f}_{lon:.5f}_{int(year)}_{signature}.json"


def _parameters(lat, lon, model, year, fields):
    dates = period.dates([year])
    lat, lon = coordinates(lat, lon)
    return {"latitude": lat, "longitude": lon, "start_date": dates[0].strftime("%Y-%m-%d"),
            "end_date": dates[-1].strftime("%Y-%m-%d"), "models": model,
            "daily": ",".join(VARIABLES[f] for f in fields), "timezone": "Asia/Shanghai",
            "temperature_unit": "celsius", "precipitation_unit": "mm", "wind_speed_unit": "ms",
            "cell_selection": "nearest", "elevation": "nan"}


def _validate(raw, fields, dates):
    try:
        if raw.get("error"):
            raise ValueError(str(raw.get("reason", "接口返回错误")))
        if raw.get("utc_offset_seconds") != 28800:
            raise ValueError("接口日界不是北京时间")
        idx = pd.DatetimeIndex(pd.to_datetime(raw["daily"]["time"], errors="raise"))
        if idx.has_duplicates or not idx.equals(dates):
            raise ValueError("历史数据日期缺失、重复或与请求不一致")
        data = {}
        for key in fields:
            var = VARIABLES[key]
            if raw["daily_units"].get(var) != UNITS[key]:
                raise ValueError(f"{var} 的单位不符合模型要求")
            values = np.array(raw["daily"][var], dtype=float)
            if len(values) != len(idx) or not np.isfinite(values).all():
                raise ValueError(f"{var} 含缺测或长度不一致")
            limits = {"tmean": (-80, 65), "tmax": (-80, 65), "tmin": (-80, 65), "rh": (0, 100),
                      "precip": (0, 1000), "ghi": (0, 60), "ws": (0, 80)}[key]
            if ((values < limits[0]) | (values > limits[1])).any():
                raise ValueError(f"{var} 超出物理有效范围")
            data[key] = values
        if all(k in data for k in ("tmin", "tmean", "tmax")):
            if ((data["tmin"] > data["tmean"]) | (data["tmean"] > data["tmax"])).any():
                raise ValueError("气温最小/平均/最大值顺序错误")
        grid = [float(raw["latitude"]), float(raw["longitude"])]
        if not np.isfinite(grid).all():
            raise ValueError("接口格点坐标无效")
        return pd.DataFrame(data, index=idx)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("历史气象响应字段不完整或格式无效") from exc


def _read(lat, lon, model, year, fields, params):
    path = _path(lat, lon, model, year, fields)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        saved = d["parameters"]
        if d.get("schema") != 1 or (saved | {"end_date": params["end_date"]}) != params:
            return None
        if not params["start_date"] <= saved["end_date"] <= params["end_date"]:
            return None
        _validate(d["response"], fields, pd.date_range(saved["start_date"], saved["end_date"]))
    except (OSError, ValueError, TypeError, KeyError):
        LOGGER.warning("忽略无效的 ERA5 缓存 %s", path.name)
        return None
    return d


def _download(request, params):
    timeouts = (8, 12, 20)
    for attempt, timeout in enumerate(timeouts, 1):
        retry = True
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(5_000_001)
        except urllib.error.HTTPError as exc:
            exc.close()
            retry = exc.code in (408, 500, 502, 503, 504)
            reason = "请求过于频繁，请稍后重试" if exc.code == 429 else f"接口返回 HTTP {exc.code}"
        except (urllib.error.URLError, OSError, IncompleteRead) as exc:
            cause = getattr(exc, "reason", exc)
            if isinstance(cause, ssl.SSLCertVerificationError):
                reason, retry = "服务器证书校验失败", False
            elif isinstance(cause, TimeoutError):
                reason = "连接或读取超时"
            elif isinstance(cause, socket.gaierror):
                reason = "无法解析接口地址"
            else:
                reason = f"连接中断（{type(cause).__name__}）"
        LOGGER.warning("ERA5 请求 %s 至 %s，第 %d 次失败：%s",
                       params["start_date"], params["end_date"], attempt, reason)
        if not retry or attempt == len(timeouts):
            break
        time.sleep(.5 * attempt)
    year = params["start_date"][:4]
    raise ValueError(f"Open-Meteo 无法获取当前坐标的 {year} 年 ERA5 气象：{reason}。")


def _fetch(lat, lon, model, year, fields):
    with LOCK:
        params = _parameters(lat, lon, model, year, fields)
        cached = _read(lat, lon, model, year, fields, params)
        if cached is not None and cached["parameters"] == params:
            return cached
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "AVOpt/1.6 historical-research"})
        try:
            blob = _download(request, params)
        except ValueError as exc:
            if cached is not None:
                return {**cached, "cache_fallback": True}
            raise ValueError(f"{exc} 可先选择该地点已缓存的年份，或稍后重试。") from None
        if len(blob) > 5_000_000:
            raise ValueError("历史气象响应过大")
        raw = json.loads(blob.decode("utf-8"))
        _validate(raw, fields, pd.date_range(params["start_date"], params["end_date"]))
        d = {"schema": 1, "provider": "Open-Meteo", "dataset": "ERA5-Land" if model == "era5_land" else "ERA5",
             "parameters": params, "request_url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
             "sha256": hashlib.sha256(blob).hexdigest(), "response": raw}
        CACHE.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE, suffix=".tmp", delete=False) as f:
            json.dump(d, f, ensure_ascii=False, allow_nan=False)
            tmp = f.name
        os.replace(tmp, _path(lat, lon, model, year, fields))
        return d


def coverage(lat, lon, years, mode):
    years = period.validate_years(years)
    models = sources(mode)
    cached_through = {}
    for item in period.catalog()["years"]:
        if not item["available"]:
            continue
        y = item["year"]
        caches = [_read(lat, lon, model, y, fields, _parameters(lat, lon, model, y, fields))
                  for model, fields in models.items()]
        if all(cache is not None for cache in caches):
            cached_through[y] = min(cache["parameters"]["end_date"] for cache in caches)
    rows = []
    for y in years:
        dates = period.dates([y])
        end = cached_through.get(y)
        cached_days = int((dates <= pd.Timestamp(end)).sum()) if end else 0
        rows.append({"year": y, "cached": cached_days == len(dates), "days": len(dates),
                     "cached_days": cached_days, "cached_through": end})
    return {"ok": True, "ready": all(x["cached"] for x in rows), "provider": "Open-Meteo",
            "mode": mode, "radiation_dataset": "ERA5", "rows": rows,
            "missing_days": sum(x["days"] - x["cached_days"] for x in rows),
            "latest_date": period.latest_date().isoformat(), "auto_download": True,
            "cached_years": cached_through,
            "complete_years": [y for y, end in cached_through.items() if end.endswith("-12-31")]}


def daily(lat, lon, years, mode, progress=None):
    years = period.validate_years(years)
    frames, provenance = [], []
    for y in years:
        pieces = []
        for model, fields in sources(mode).items():
            if progress:
                progress(f"{y} {model}")
            d = _fetch(lat, lon, model, y, fields)
            params = d["parameters"]
            pieces.append(_validate(d["response"], fields, pd.date_range(params["start_date"], params["end_date"])))
            provenance.append({k: v for k, v in d.items() if k != "response"} | {
                "response_grid": [d["response"]["latitude"], d["response"]["longitude"]],
                "fields": fields, "native_resolution_deg": .1 if model == "era5_land" else .25})
        frames.append(pd.concat(pieces, axis=1))
    df = pd.concat(frames).sort_index().rename_axis("date")
    df["ws_10m"] = df["ws"]
    df["ws"] *= WIND_10M_TO_2M
    return df, provenance


def build(place, lat, lon, years, mode):
    df, provenance = daily(lat, lon, years, mode)
    w = {k: df[k].to_numpy(dtype=float) for k in VARIABLES}
    w["date_ordinal"] = np.array([d.toordinal() for d in df.index], dtype=float)
    partial = []
    warnings = []
    for year, frame in df.groupby(df.index.year):
        end = frame.index[-1]
        if (end.month, end.day) != (12, 31):
            partial.append(int(year))
            warnings.append(f"{year} 年按实际气象数据计算至 {end:%Y-%m-%d}，发电量与收益为时段累计值。")
    for item in provenance:
        if item.get("cache_fallback"):
            warnings.append(f"联网更新失败，本次采用当前坐标截至 {item['parameters']['end_date']} 的 ERA5 缓存。")
    radiation_grid = next(p["response_grid"] for p in provenance if p["dataset"] == "ERA5")
    return w, {"place": place, "years": list(map(int, sorted(set(df.index.year)))), "days": len(df),
               "date_start": df.index.min().strftime("%Y-%m-%d"), "date_end": df.index.max().strftime("%Y-%m-%d"),
               "source": "Open-Meteo · ERA5（全部气象）", "calculation_basis": "actual_period", "humidity_source": "ERA5 日均相对湿度",
               "wind_source": "ERA5 10 m 日均风速，按 FAO-56 换算为模型所需 2 m 风速",
               "radiation": {"dataset": "ERA5", "provider": "Open-Meteo", "variable": VARIABLES["ghi"],
                             "grid": radiation_grid, "native_resolution_deg": .25, "unit": "MJ/m²/d", "day_timezone": "UTC+08:00"},
               "provenance": provenance, "partial_years": partial, "quality": {"missing_days": 0},
               "warnings": warnings}


def export_csv(lat, lon, years, mode):
    df, _ = daily(lat, lon, years, mode)
    df["ghi_kwh_m2"] = df["ghi"] / 3.6
    df["radiation_dataset"] = "ERA5"
    df["temperature_dataset"] = "ERA5"
    df = df.rename(columns={"tmean":"tmean_C", "tmax":"tmax_C", "tmin":"tmin_C", "rh":"rh_pct", "precip":"precip_mm", "ghi":"ghi_MJ_m2", "ws":"wind_2m_m_s", "ws_10m":"wind_10m_m_s"})
    return df.to_csv(index=True).encode("utf-8-sig")
