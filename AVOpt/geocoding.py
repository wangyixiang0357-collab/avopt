"""本地行政区多边形反查；只下载区域边界，不向第三方发送用户坐标。"""
import json
import math
import threading
import urllib.request
from functools import lru_cache
from pathlib import Path
from . import weather_cn

CACHE = Path(__file__).resolve().parents[1] / "data/admin_boundaries"
SOURCE = "https://geo.datav.aliyun.com/areas_v3/bound/"
LOCK = threading.Lock()

def wgs84_to_gcj02(lon, lat):
    """与 DataV GCJ-02 边界对齐；气象和太阳几何仍使用原 WGS84 坐标。"""
    if not (72.004 <= lon <= 137.8347 and .8293 <= lat <= 55.8271):
        return lon, lat
    x, y = lon - 105., lat - 35.
    pi = math.pi
    dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    dlat += (20*math.sin(6*x*pi) + 20*math.sin(2*x*pi))*2/3
    dlat += (20*math.sin(y*pi) + 40*math.sin(y*pi/3))*2/3
    dlat += (160*math.sin(y*pi/12) + 320*math.sin(y*pi/30))*2/3
    dlon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    dlon += (20*math.sin(6*x*pi) + 20*math.sin(2*x*pi))*2/3
    dlon += (20*math.sin(x*pi) + 40*math.sin(x*pi/3))*2/3
    dlon += (150*math.sin(x*pi/12) + 300*math.sin(x*pi/30))*2/3
    rad = lat*pi/180
    magic = 1 - .006693421622965943 * math.sin(rad)**2
    root = math.sqrt(magic)
    dlat = dlat*180 / ((6378245.*(1-.006693421622965943))/(magic*root)*pi)
    dlon = dlon*180 / (6378245./root*math.cos(rad)*pi)
    return lon + dlon, lat + dlat

@lru_cache(maxsize=128)
def boundaries(code):
    code = str(code)
    if not (len(code) == 6 and code.isdigit()):
        raise ValueError("行政区代码格式无效")
    with LOCK:
        CACHE.mkdir(exist_ok=True, parents=True)
        path = CACHE / f"{code}_full.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            try:
                request = urllib.request.Request(SOURCE + path.name, headers={"User-Agent": "AVOpt/1.2"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read(15_000_000)
                data = json.loads(raw)
                if not isinstance(data.get("features"), list):
                    raise ValueError("边界数据格式无效")
                temp = path.with_suffix(".tmp")
                temp.write_bytes(raw); temp.replace(path)
            except Exception as exc:
                raise RuntimeError("行政区边界暂未缓存且下载失败；请联网重试，或手动检索行政区。" + str(exc)) from exc
    return data["features"]

def in_ring(x, y, ring):
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        ax, ay = a[:2]; bx, by = b[:2]
        cross = (x-ax)*(by-ay) - (y-ay)*(bx-ax)
        if abs(cross) <= 1e-11 and min(ax,bx)-1e-11 <= x <= max(ax,bx)+1e-11 and min(ay,by)-1e-11 <= y <= max(ay,by)+1e-11:
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside

def contains(feature, lon, lat):
    geometry = feature.get("geometry") or {}
    if geometry.get("type") == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry.get("type") == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        return False
    return any(rings and in_ring(lon, lat, rings[0]) and not any(in_ring(lon, lat, hole) for hole in rings[1:]) for rings in polygons)

def unique_match(features, lon, lat):
    hits = [f for f in features if f.get("properties", {}).get("adcode") and contains(f, lon, lat)]
    if not hits:
        raise ValueError("该坐标未匹配到支持的行政区边界；请核对坐标或手动选区")
    if len(hits) != 1:
        raise ValueError("坐标位于行政区边界附近，请手动确认区县")
    return hits[0]

def reverse(lat, lon, coordinate_system="WGS84"):
    try:
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError):
        raise ValueError("请填写有效经纬度") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("经纬度超出有效范围")
    if coordinate_system not in ("WGS84", "GCJ02"):
        raise ValueError("坐标系只支持 WGS84 或 GCJ02")
    x, y = wgs84_to_gcj02(lon, lat) if coordinate_system == "WGS84" else (lon, lat)
    feature = unique_match(boundaries("100000"), x, y)
    for _ in range(3):
        prop = feature["properties"]
        if prop.get("level") == "district" or prop.get("childrenNum") == 0:
            break
        feature = unique_match(boundaries(str(prop["adcode"])), x, y)
    code = str(feature["properties"]["adcode"])
    item = next((dict(p) for p in weather_cn.load_address() if str(p["areaCode"]) == code), None)
    if item is None:
        raise ValueError(f"已定位到 {feature['properties']['name']}（{code}），但当前气象地址库无对应站点，请手动选择邻近气象行政区")
    item.update(lat=lat, lon=lon, matched_lat=lat, matched_lon=lon,
                coordinate_system=coordinate_system, coordinate_note="按公开区县边界自动匹配；边界附近请核对实际地块")
    return item
