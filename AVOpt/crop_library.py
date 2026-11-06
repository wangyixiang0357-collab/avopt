"""作物情景模板。新增夏作参数不是项目实测或已验证的品种参数。"""
from AVSim.photosynthesis import PhotoParams


CROPS = {
    "冬小麦": dict(name="冬小麦", yield_target=6750, sow_doy=283, harvest_doy=156, lai_max=5.0,
                k=.50, hi=.45, tbase=0., kc=(.40, 1.15, .30)),
    "冬油菜": dict(name="冬油菜", yield_target=3000, sow_doy=263, harvest_doy=140, lai_max=4.0,
                k=.55, hi=.28, tbase=5., kc=(.35, 1.10, .35), tmax_lim=32.),
    "夏玉米": dict(name="夏玉米", yield_target=9000, sow_doy=166, harvest_doy=273, lai_max=5.5,
                k=.50, hi=.50, tbase=8., tcap=40., tmax_lim=42., kc=(.30, 1.20, .35),
                par_k=12., shade_rue_gain=0., shade_lai_gain=.05),
    "大豆": dict(name="大豆", yield_target=3000, sow_doy=166, harvest_doy=278, lai_max=4.0,
                k=.60, hi=.40, tbase=10., tcap=35., tmax_lim=40., kc=(.40, 1.15, .50)),
    "花生": dict(name="花生", yield_target=4500, sow_doy=161, harvest_doy=278, lai_max=4.0,
                k=.60, hi=.35, tbase=10., tcap=35., tmax_lim=40., kc=(.40, 1.15, .60)),
}

# 光响应与胁迫系数为简化情景初值。光合途径决定饱和和温度响应的参数化。
PHOTOSYNTHESIS = {
    "冬小麦": PhotoParams("C3", 340., 28., 42., 1.6, .26, 1250., .16),
    "冬油菜": PhotoParams("C3", 320., 27., 39., 1.5, .28, 1150., .17),
    "夏玉米": PhotoParams("C4", 560., 35., 48., 2.4, .14, 1600., .09),
    "大豆": PhotoParams("C3", 380., 32., 44., 1.9, .25, 1200., .15),
    "花生": PhotoParams("C3", 400., 33., 45., 2.0, .23, 1250., .14),
}

# 单价均为可编辑情景值，不是实时行情；花生按荚果计价，避免与花生仁口径混用。
CROP_META = {
    "冬小麦": dict(price=2.4, season="10月10日—次年6月5日", product="小麦籽粒", status="沿用原项目模板"),
    "冬油菜": dict(price=5.5, season="9月20日—次年5月20日", product="油菜籽", status="沿用原项目模板"),
    "夏玉米": dict(price=2.4, season="6月15日—9月30日", product="玉米籽粒", status="新增夏季情景模板，需按当地品种校准"),
    "大豆": dict(price=4.6, season="6月15日—10月5日", product="大豆籽粒", status="新增夏季情景模板，需按当地品种校准"),
    "花生": dict(price=6.0, season="6月10日—10月5日", product="花生荚果（非花生仁）", status="新增夏季情景模板，需按当地品种校准"),
}

def metadata():
    return {name: {**info, "yield_target": CROPS[name]["yield_target"], "kc": CROPS[name]["kc"]} for name, info in CROP_META.items()}
