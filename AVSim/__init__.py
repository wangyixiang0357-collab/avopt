"""AVSim —— 农光互补三维光环境—作物—水分耦合模拟与布局优化系统.

模块划分
    config    参数对象（站点、阵列、作物、经济）
    solar     太阳位置、辐射拆分与逐时分解
    shade     阵列遮阴几何、天空视角因子、分区采光率
    crop      物候、LAI、辐射利用效率与产量
    water     参考蒸散与土壤水量平衡
    pv        倾斜面辐照、组件温度与发电量
    econ      土地当量比与经济效益
    optimize  布局参数寻优
    io_utils  气象数据读取与结果导出
"""

__version__ = "1.0.0"
__all__ = ["config", "solar", "shade", "crop", "water", "pv", "econ", "optimize", "io_utils"]
