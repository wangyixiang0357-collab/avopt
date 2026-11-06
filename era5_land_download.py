"""官方 ERA5-Land 单点辐射下载；凭据只从本机 CDS 配置读取。"""
import argparse
import json
import sys
from pathlib import Path

runtime = Path(__file__).resolve().parent / ".runtime"
if runtime.exists():
    sys.path.insert(0, str(runtime))
from AVOpt.era5_land import download_cds, cds_request, DATASET

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--years", type=int, nargs="+", required=True)
    p.add_argument("--request-only", action="store_true", help="只输出请求，不连接 CDS")
    args = p.parse_args()
    if args.request_only:
        print(json.dumps({"dataset": DATASET, "requests": [cds_request(args.lat, args.lon, y) for y in args.years]}, indent=2))
    else:
        print(json.dumps(download_cds(args.lat, args.lon, args.years), ensure_ascii=False, indent=2))
