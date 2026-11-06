"""半小时冠层光合近似，输出可由 RUE 换算为干物质的等效吸收 PAR。"""
from dataclasses import dataclass

import numpy as np

from . import crop, solar
from .water import saturation_vapour_pressure


STEP_H = 0.5
PAR_FRACTION = .45
PHOTONS_PER_JOULE = 4.57  # μmol/J PAR


@dataclass(frozen=True)
class PhotoParams:
    pathway: str
    light_scale: float      # 吸收 PPFD 的光饱和尺度，μmol/(m²叶·s)
    heat_start: float       # 叶温开始限制光合，℃
    heat_stop: float
    vpd_start: float        # 午间气孔限制起点，kPa
    midday_loss: float      # 午间 VPD 限制的最大折减
    high_light: float       # 局部入射 PPFD 的过量光起点
    high_light_loss: float


def atmosphere(weather, doys, site):
    """由日辐射和最低/最高温近似重建半小时环境。"""
    sun = solar.hourly_from_daily(weather["ghi"], doys, site.lat, site.lon, site.tz, step=STEP_H)
    solar_hour = (sun["hour"] + solar.equation_of_time(doys)[:, None] / 60
                  + site.lon / 15 - site.tz) % 24
    warm = .5 + .5 * np.cos(2 * np.pi * (solar_hour - 14) / 24)
    low = np.asarray(weather["tmin"])[:, None]
    high = np.asarray(weather["tmax"])[:, None]
    return {
        "beam": sun["dni"] * np.maximum(sun["u"], 0),
        "diffuse": sun["dhi"],
        "air_temp": low + (high - low) * warm,
        "vapour": (saturation_vapour_pressure(np.asarray(weather["tmean"])) * np.asarray(weather["rh"]) / 100)[:, None],
        "midday": np.exp(-.5 * ((solar_hour - 12.5) / 2.2) ** 2),
        # 一阶辐射增温近似，风速较大时同等光照的叶温增量较小。
        "warming": (2.5 / (1 + .25 * np.asarray(weather["ws"])))[:, None],
    }


def effective_par(cfg, params, shortwave, air, ks, stage, shade_factor=0.):
    """三层 Beer–Lambert 冠层，返回基准和胁迫后的逐日等效 PAR。

    基准含光合饱和、低温与根区水分限制，供完整生育季校准。
    实际值另计高温、午间 VPD 和高光折减，供露地及阵列分区统一使用。
    """
    lai = crop.lai_curve(stage, cfg.lai_max * (1 + cfg.shade_lai_gain * shade_factor))
    layer_lai = lai[:, None] / 3
    attenuation = cfg.k * layer_lai
    absorbed_fraction = -np.expm1(-attenuation)
    incident_par = PAR_FRACTION * np.maximum(shortwave, 0)
    cold = np.clip((air["air_temp"] - cfg.tbase) / 7, 0, 1)
    potential = np.zeros(len(lai))
    actual = np.zeros(len(lai))
    for layer in range(3):
        top = np.exp(-layer * attenuation)
        mid = np.exp(-(layer + .5) * attenuation)
        absorbed = incident_par * top * absorbed_fraction
        light = PHOTONS_PER_JOULE * absorbed / np.maximum(layer_lai, 1e-9)
        response = light / params.light_scale
        saturation = np.divide(-np.expm1(-response), response,
                               out=np.ones_like(response), where=response > 0)
        base = absorbed * saturation * cold

        leaf_temp = air["air_temp"] + air["warming"] * np.clip(shortwave * mid / 800, 0, 1)
        heat = np.clip((leaf_temp - params.heat_start) / (params.heat_stop - params.heat_start), 0, 1)
        vpd = np.maximum(saturation_vapour_pressure(leaf_temp) - air["vapour"], 0)
        midday = 1 - params.midday_loss * air["midday"] * np.clip((vpd - params.vpd_start) / 2.5, 0, 1)
        excess = np.clip((PHOTONS_PER_JOULE * incident_par * mid - params.high_light) / 700, 0, 1)
        high_light = 1 - params.high_light_loss * excess ** 2

        potential += base.sum(axis=1)
        actual += (base * (1 - heat ** 2) * midday * high_light).sum(axis=1)
    conversion = STEP_H * 3600 / 1e6 * np.asarray(ks)
    return potential * conversion, actual * conversion
