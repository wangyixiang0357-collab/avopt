"""网页数据范围固定为 2020—2026；当年只请求已发布的历史日。"""
from datetime import datetime, timedelta, timezone
import pandas as pd

MIN_YEAR, MAX_YEAR = 2020, 2026
DELAY_DAYS = 6  # ERA5/ERA5-Land 通常延迟 5 日，额外留 1 日保证完整北京时间日。


def today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def latest_date():
    return min(today() - timedelta(days=DELAY_DAYS), datetime(MAX_YEAR, 12, 31).date())


def validate_years(years):
    if not isinstance(years, (list, tuple)) or not 1 <= len(years) <= MAX_YEAR-MIN_YEAR+1:
        raise ValueError("请选择 2020—2026 年内的气象年份")
    try:
        if any(isinstance(y, bool) or int(y) != float(y) for y in years):
            raise ValueError()
        result = sorted(set(int(y) for y in years))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("年份格式无效") from None
    if any(y < MIN_YEAR or y > MAX_YEAR or y > latest_date().year for y in result):
        raise ValueError("仅支持 2020—2026 年中已经发布的历史气象数据")
    return result


def dates(years):
    years = validate_years(years)
    end = pd.Timestamp(latest_date())
    return pd.DatetimeIndex([d for y in years for d in pd.date_range(f"{y}-01-01", min(pd.Timestamp(f"{y}-12-31"), end))])


def catalog():
    end = latest_date()
    return {"min_year": MIN_YEAR, "max_year": MAX_YEAR, "latest_date": end.isoformat(),
            "years": [{"year": y, "complete": y < end.year or (y == end.year and (end.month, end.day) == (12, 31)),
                       "available": y <= end.year,
                       "end_date": min(datetime(y, 12, 31).date(), end).isoformat() if y <= end.year else None}
                      for y in range(MIN_YEAR, MAX_YEAR+1)]}
