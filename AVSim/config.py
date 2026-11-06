"""参数对象定义。

所有模块只读取这里的字段，不修改，便于批量方案计算与参数敏感性分析。
"""
from dataclasses import dataclass, field, asdict


@dataclass
class SiteConfig:
    """站点与气象参数。"""
    name: str = "大荔"
    lat: float = 34.70          # 纬度，度
    lon: float = 110.07         # 经度，度
    tz: int = 8                 # 时区
    elevation: float = 350.0    # 海拔，m
    albedo: float = 0.20        # 地表反照率
    annual_rain: float = 514.0  # 年降水，mm（仅作校验）


@dataclass
class ArrayConfig:
    """固定式光伏阵列几何参数。

    tilt   组件倾角，度
    height 组件下沿离地高度，m
    span   阵列跨度（相邻两排组件下沿的南北间距），m
    width  单排组件沿坡面宽度，m
    rows   计算时向南北两侧扩展的排数
    """
    tilt: float = 28.0
    height: float = 3.0
    span: float = 12.9   # 阵列间距=板间净宽 9.0 m + 组件投影 3.9 m
    width: float = 4.4
    rows: int = 6
    structure_loss: float = 0.06  # 边框、檩条与端部遮挡的综合折减
    module_eff: float = 0.21    # 组件效率
    noct: float = 45.0          # 标称工作温度，℃
    temp_coef: float = -0.004   # 功率温度系数，1/℃
    system_loss: float = 0.15   # 系统综合损失

    @property
    def gcr(self) -> float:
        """垂直投影覆盖率（组件投影面积/农地面积）。"""
        import math
        return self.width * math.cos(math.radians(self.tilt)) / self.span


@dataclass
class CropConfig:
    """作物参数。

    name      作物名称
    sow_doy   播种日序（1—366）
    harvest_doy 收获日序，跨年时小于 sow_doy
    tbase     生物学零度，℃
    tcap      上限温度，℃
    lai_max   最大叶面积指数
    k         冠层消光系数
    rue       辐射利用效率，g 干物质/MJ PAR
    hi        收获指数
    kc        各生育阶段作物系数（初期、中期、后期）
    yield_target 露地目标产量，kg/hm²
    """
    name: str = "冬小麦"
    sow_doy: int = 283
    harvest_doy: int = 156
    tbase: float = 0.0
    tcap: float = 32.0
    tmax_lim: float = 35.0      # 日均温高温修正上限，℃
    lai_max: float = 5.0
    k: float = 0.50
    rue: float = 3.0
    hi: float = 0.45
    kc: tuple = (0.40, 1.15, 0.30)
    yield_target: float = 6750.0
    par_k: float = 6.0             # 冠层日光合半饱和参数，MJ/(m²·d)
    shade_rue_gain: float = 0.05   # 遮阴下光能利用的上调比例
    shade_lai_gain: float = 0.10   # 遮阴下 LAI 的上调比例


@dataclass
class SoilConfig:
    """土壤水分参数（0—100 cm 根区）。"""
    fc: float = 0.30       # 田间持水量，体积含水率
    wp: float = 0.13       # 凋萎点
    zr: float = 1.0        # 根区深度，m
    raw_frac: float = 0.50 # 易利用水占可利用水比例

    @property
    def taw(self) -> float:
        return (self.fc - self.wp) * self.zr * 1000.0


@dataclass
class EconConfig:
    """经济参数（元/kg、元/kWh、元/hm²）。"""
    wheat_price: float = 2.40
    rape_price: float = 5.50
    tariff: float = 0.35
    pv_capex_per_w: float = 3.00
    pv_life: int = 25
    pv_om_per_w_year: float = 0.04
    ag_cost_per_ha: float = 6000.0
    discount: float = 0.06
    land_rent_per_ha: float = 0.0  # 年租金，元/(hm²·a)


@dataclass
class Scenario:
    """一个完整计算方案。"""
    site: SiteConfig = field(default_factory=SiteConfig)
    array: ArrayConfig = field(default_factory=ArrayConfig)
    crop: CropConfig = field(default_factory=CropConfig)
    soil: SoilConfig = field(default_factory=SoilConfig)
    econ: EconConfig = field(default_factory=EconConfig)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["array"]["gcr"] = self.array.gcr
        d["soil"]["taw"] = self.soil.taw
        return d
