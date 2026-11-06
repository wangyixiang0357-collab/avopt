"""ERA5-Land SSRD：官方 CDS 接口、CSV 导入与独立缓存。无 NASA 回退。

ARCO 点位时序的 SSRD 已去累积；原始小时产品须按 00 UTC 预报周期去累积。
小时值对应截至时间戳的前一小时，统一按 UTC+8 日历日汇总为 MJ/m²/d。
"""
import hashlib
import io
import json
import math
import os
import tempfile
import threading
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from . import period

DATASET = "reanalysis-era5-land-timeseries"
VARIABLE = "surface_solar_radiation_downwards"
CACHE = Path(__file__).resolve().parents[1] / "data" / "era5_land"
LOCK = threading.RLock()
FORMATS = {"daily_mj", "daily_j", "daily_kwh", "arco_hourly_j", "cumulative_hourly_j"}


def point(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError):
        raise ValueError("请填写有效的辐射数据经纬度") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon < 180):
        raise ValueError("辐射数据经纬度超出范围")
    return round(lat, 1), round(lon, 1)


def _path(lat, lon):
    lat, lon = point(lat, lon)
    return CACHE / f"ssrd_{lat:.1f}_{lon:.1f}.json"


def _read(lat, lon):
    path = _path(lat, lon)
    if not path.exists():
        return {"schema": 1, "dataset": "ERA5-Land", "grid": list(point(lat, lon)),
                "unit": "MJ/m²/d", "day_timezone": "UTC+08:00", "records": {}, "imports": {}}
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("schema") != 1 or d.get("dataset") != "ERA5-Land" or d.get("day_timezone") != "UTC+08:00":
        raise ValueError("ERA5-Land 缓存格式不兼容，请重新导入")
    return d


def expected_dates(years):
    return period.dates(years)


def status(lat, lon, years):
    dates = expected_dates(years)
    with LOCK:
        d = _read(lat, lon)
    missing = [t.strftime("%Y-%m-%d") for t in dates if t.strftime("%Y-%m-%d") not in d["records"]]
    available = sorted({int(k[:4]) for k in d["records"]})
    complete = [y for y in available if all(t.strftime("%Y-%m-%d") in d["records"] for t in pd.date_range(f"{y}-01-01", f"{y}-12-31"))]
    return {"ok": True, "dataset": "ERA5-Land", "grid": d["grid"], "days": len(d["records"]),
            "complete_years": complete, "missing_days": len(missing), "missing_examples": missing[:3],
            "ready": not missing, "day_timezone": d["day_timezone"]}


def load(lat, lon, years):
    dates = expected_dates(years)
    with LOCK:
        d = _read(lat, lon)
    keys = [t.strftime("%Y-%m-%d") for t in dates]
    missing = [k for k in keys if k not in d["records"]]
    if missing:
        raise ValueError(f"ERA5-Land 辐射尚缺 {len(missing)} 天（如 {', '.join(missing[:3])}）。请在左侧导入对应格点和年份的辐射 CSV，或配置 CDS 后使用下载工具。")
    s = pd.Series([d["records"][k]["ghi"] for k in keys], index=dates, dtype=float)
    if not np.isfinite(s).all() or not s.between(0, 60).all():
        raise ValueError("ERA5-Land 缓存辐射异常，请重新导入")
    refs = sorted({d["records"][k]["import_id"] for k in keys})
    s.attrs["radiation"] = {"dataset": "ERA5-Land", "variable": VARIABLE, "grid": d["grid"],
                           "unit": d["unit"], "day_timezone": d["day_timezone"],
                           "imports": [d["imports"][ref] for ref in refs]}
    return s


def parse_csv(csv_text, fmt, lat, lon):
    """明确选择单位/累积口径；无坐标列时采用用户声明的当前点位。"""
    grid = point(lat, lon)
    if fmt not in FORMATS:
        raise ValueError("未知辐射 CSV 格式")
    if not isinstance(csv_text, str) or not csv_text.strip() or len(csv_text.encode("utf-8")) > 7_000_000:
        raise ValueError("辐射 CSV 为空或超过 7 MB；请按年分批导入")
    try:
        df = pd.read_csv(io.StringIO(csv_text.lstrip("\ufeff")), comment="#")
        df.columns = [str(c).strip().lower() for c in df.columns]
        tc = next(c for c in ("valid_time", "time", "date", "datetime") if c in df)
        vc = next(c for c in (VARIABLE, "ssrd", "ghi") if c in df)
    except (ValueError, StopIteration, pd.errors.ParserError):
        raise ValueError("CSV 需包含 date/time 和 ssrd（或 surface_solar_radiation_downwards）列") from None
    if df.empty:
        raise ValueError("CSV 没有数据行")
    coordinate_source = "用户声明当前地块对应的 0.1° 最近格点"
    for aliases, expected in ((("latitude", "lat"), grid[0]), (("longitude", "lon"), grid[1])):
        col = next((c for c in aliases if c in df), None)
        if col:
            vals = pd.to_numeric(df[col], errors="coerce")
            if not np.isfinite(vals).all() or vals.nunique() != 1 or abs(float(vals.iloc[0]) - expected) > .001:
                raise ValueError("CSV 坐标必须为当前地块最近的单一 0.1° 格点，不能混入其他位置")
            coordinate_source = "CSV 坐标列与最近格点核验"
    try:
        times = pd.DatetimeIndex(pd.to_datetime(df[tc], errors="raise", utc=True))
        values = pd.to_numeric(df[vc], errors="raise").to_numpy(dtype=float)
    except (ValueError, TypeError):
        raise ValueError("CSV 日期或辐射值格式无效") from None
    if times.hasnans or times.has_duplicates or not np.isfinite(values).all() or (values < -.01).any():
        raise ValueError("CSV 含重复时间、缺测、负数或非有限辐射值")
    values = np.maximum(values, 0)
    s = pd.Series(values, index=times).sort_index()
    dropped = []
    if fmt.startswith("daily_"):
        if not (times == times.normalize()).all():
            raise ValueError("逐日 CSV 的 date 只填 YYYY-MM-DD，按北京时间日期解释")
        s.index = s.index.tz_localize(None)
        s *= {"daily_mj": 1., "daily_j": 1e-6, "daily_kwh": 3.6}[fmt]
    else:
        if ((s.index.minute != 0) | (s.index.second != 0) | (s.index.microsecond != 0)).any():
            raise ValueError("小时 CSV 时间必须对齐整点（UTC）")
        if fmt == "cumulative_hourly_j":
            diff = s.diff()
            consecutive = s.index.to_series().diff().eq(pd.Timedelta(hours=1))
            diff.loc[~consecutive] = np.nan
            diff.loc[s.index.hour == 1] = s.loc[s.index.hour == 1]
            if (diff.dropna() < -.01).any():
                raise ValueError("预报日内累积值递减，请核对是否误选了原始累积格式")
            s = diff.clip(lower=0)
        # 结束时刻减 1ns，再转 UTC+8；00:00 的小时值属于前一日。
        day = (s.index + pd.Timedelta(hours=8) - pd.Timedelta(nanoseconds=1)).tz_localize(None).normalize()
        counts = s.groupby(day).count()
        totals = s.groupby(day).sum(min_count=24) / 1e6
        bad = counts[counts != 24]
        interior = bad.index.difference([counts.index[0], counts.index[-1]])
        if len(interior):
            raise ValueError(f"小时序列存在不完整日（如 {interior[0]:%Y-%m-%d}），请补齐 24 小时")
        dropped = [t.strftime("%Y-%m-%d") for t in bad.index]
        s = totals.loc[counts == 24]
    if s.empty or not s.between(0, 60).all():
        raise ValueError("没有完整日，或日辐射超出 0～60 MJ/m²/d；请检查单位和累积口径")
    if len(s.index) != len(pd.date_range(s.index.min(), s.index.max())):
        raise ValueError("CSV 日期不连续，请补齐缺日后导入")
    return s, {"format": fmt, "coordinate_source": coordinate_source, "dropped_edge_days": dropped}


def import_csv(csv_text, fmt, lat, lon, filename="local.csv", origin="local_import"):
    s, details = parse_csv(csv_text, fmt, lat, lon)
    sha = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
    ref = hashlib.sha256((sha + fmt).encode()).hexdigest()[:24]
    provenance = {**details, "sha256": sha, "file": Path(str(filename).replace("\\", "/")).name[:200],
                  "origin": origin, "imported_at": datetime.now(timezone.utc).isoformat(),
                  "start": s.index.min().strftime("%Y-%m-%d"), "end": s.index.max().strftime("%Y-%m-%d"),
                  "days": len(s), "declared_dataset": "ERA5-Land"}
    with LOCK:
        d = _read(lat, lon)
        d["imports"][ref] = provenance
        for t, value in s.items():
            d["records"][t.strftime("%Y-%m-%d")] = {"ghi": float(value), "import_id": ref}
        CACHE.mkdir(parents=True, exist_ok=True)
        path = _path(lat, lon)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE, delete=False, suffix=".tmp") as f:
            json.dump(d, f, ensure_ascii=False, allow_nan=False)
            temp = f.name
        os.replace(temp, path)
    return {"ok": True, "imported_days": len(s), "start": provenance["start"], "end": provenance["end"],
            "grid": d["grid"], "dropped_edge_days": details["dropped_edge_days"]}


def cds_request(lat, lon, year):
    """多取前一天，供北京时间年初的前几个小时使用。"""
    dates = expected_dates([year])
    lat, lon = point(lat, lon)
    return {"variable": [VARIABLE], "location": {"latitude": lat, "longitude": lon},
            "date": f"{int(year)-1}-12-31/{dates[-1]:%Y-%m-%d}", "data_format": "csv"}


def download_cds(lat, lon, years):
    """使用本机 .cdsapirc；不接收/记录网页传入的密钥，也不代替用户接受许可。"""
    expected_dates(years)
    try:
        import cdsapi
    except ImportError:
        raise ValueError("请先安装 cdsapi，并在本机配置 .cdsapirc；也可直接使用网页 CSV 导入") from None
    client = cdsapi.Client()
    results = []
    with tempfile.TemporaryDirectory(prefix="avopt_era5_") as tmp:
        for year in sorted(set(map(int, years))):
            target = Path(tmp) / f"ssrd_{year}.download"
            client.retrieve(DATASET, cds_request(lat, lon, year), str(target))
            if zipfile.is_zipfile(target):
                with zipfile.ZipFile(target) as z:
                    csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
                    if len(csvs) != 1 or z.getinfo(csvs[0]).file_size > 7_000_000:
                        raise ValueError("CDS 返回的文件不符合单点单年 CSV 格式")
                    raw = z.read(csvs[0]).decode("utf-8-sig")
            else:
                raw = target.read_text(encoding="utf-8-sig")
            results.append(import_csv(raw, "arco_hourly_j", lat, lon, f"CDS_ssrd_{year}.csv", "official_cds_api"))
    load(lat, lon, years)  # 只有所选年份已完整覆盖，才报告下载成功。
    return results
