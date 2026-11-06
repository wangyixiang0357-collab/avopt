"""土地当量比与经济效益。"""
import numpy as np


def land_equivalent_ratio(crop_yield_av, crop_yield_ck, energy_av, energy_ref):
    """土地当量比 LER = 作物相对产量 + 发电相对量。

    分母取同面积露地单作与同倾角、冬至设计间距参照光伏的年产出，
    两项之和大于 1 表示复合利用优于任一单用途。
    """
    return crop_yield_av / max(crop_yield_ck, 1e-9) + energy_av / max(energy_ref, 1e-9)


def annual_income(yield_kg_ha, price, energy_kwh_ha, tariff,
                  pv_capex_per_w, pv_life, pv_om_per_w_year, ag_cost_per_ha,
                  capacity_kwp_ha):
    """年净收益，元/(hm²·a)。

    光伏成本按造价除以寿命直线年摊销，不计残值和折现。
    """
    crop_income = yield_kg_ha * price
    energy_income = energy_kwh_ha * tariff
    capex = capacity_kwp_ha * 1000.0 * pv_capex_per_w
    annuity = capex / pv_life
    om = capacity_kwp_ha * 1000.0 * pv_om_per_w_year
    return {"crop_income": crop_income, "energy_income": energy_income,
            "pv_annuity": annuity, "pv_om": om, "ag_cost": ag_cost_per_ha,
            "net": crop_income + energy_income - annuity - om - ag_cost_per_ha}
