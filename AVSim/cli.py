"""命令行入口：python -m AVSim.cli --config config.json --out results/"""
import argparse
import json
import os
import sys

from .config import Scenario, SiteConfig, ArrayConfig, CropConfig, SoilConfig, EconConfig
from . import io_utils, model


def build_scenario(cfg: dict) -> Scenario:
    sc = Scenario()
    if "site" in cfg:
        sc.site = SiteConfig(**cfg["site"])
    if "array" in cfg:
        sc.array = ArrayConfig(**cfg["array"])
    if "crop" in cfg:
        c = dict(cfg["crop"])
        if "kc" in c:
            c["kc"] = tuple(c["kc"])
        sc.crop = CropConfig(**c)
    if "soil" in cfg:
        sc.soil = SoilConfig(**cfg["soil"])
    if "econ" in cfg:
        sc.econ = EconConfig(**cfg["econ"])
    return sc


def main(argv=None):
    ap = argparse.ArgumentParser(description="农光互补光—水—作物耦合模拟与布局优化系统")
    ap.add_argument("--config", required=True, help="方案参数 JSON")
    ap.add_argument("--weather", required=True, help="逐日气象 JSON（NASA POWER 格式）")
    ap.add_argument("--out", default="results", help="输出目录")
    ap.add_argument("--tmean-target", type=float, default=None, help="年均气温订正目标，℃")
    ap.add_argument("--precip-target", type=float, default=None, help="年降水订正目标，mm")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    df = io_utils.load_power_json(args.weather)
    if args.tmean_target or args.precip_target:
        df = io_utils.bias_correct(df, args.tmean_target, args.precip_target)
    weather = io_utils.typical_year(df)

    with open(args.config, encoding="utf-8") as f:
        sc = build_scenario(json.load(f))

    res = model.run(sc, weather)
    out = os.path.join(args.out, "result.json")
    io_utils.export_json(out, res)
    print("作物 %s：露地 %.0f kg/hm²，区域加权 %.0f kg/hm²，LER %.3f" % (
        sc.crop.name, res["crop"]["ck_yield"], res["crop"]["area_yield"], res["ler"]))
    print("结果已写入", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
