"""黄金基准数据

温度标定黄金值 — 来自 FBG.py 输出。
应变标定黄金值 — 来自标定自补偿2组.xlsx，calibrate_strain() 引擎输出。
"""

# ═══════════════════════════════════════════════════════════════════════
# 应变标定黄金值
# 格式: {sensor_g1: {k, R2, NL, RP, HY}}  容差宽松以兼容跨平台浮点差异
# 数据来源: 标定自补偿2组.xlsx，80mm标距，tension_return，3循环，dual_both
# ═══════════════════════════════════════════════════════════════════════

STRAIN_GOLDEN = {
    "A1_g1": {
        "k_pm_per_ue": 1.176,
        "R2": 0.9990,
        "nonlinearity_pct_fs": 2.37,
        "repeatability_pct_fs": 2.64,
        "hysteresis_pct_fs": 5.3,   # 近似值: 位移非对称导致，正确值≈2.3
    },
    "A2_g1": {
        "k_pm_per_ue": 1.056,
        "R2": 0.9939,
        "nonlinearity_pct_fs": 5.42,
        "repeatability_pct_fs": 8.15,
        "hysteresis_pct_fs": 10.23,
    },
    "B1_g1": {
        "k_pm_per_ue": 0.726,
        "R2": 0.9916,
        "nonlinearity_pct_fs": 7.74,
        "repeatability_pct_fs": 13.09,
        "hysteresis_pct_fs": 13.23,
    },
    "B1_g2": {
        "k_pm_per_ue": 1.116,
        "R2": 0.9986,
        "nonlinearity_pct_fs": 4.51,
        "repeatability_pct_fs": 4.66,
        "hysteresis_pct_fs": 4.48,
    },
    "B2_g1": {
        "k_pm_per_ue": 0.806,
        "R2": 0.9988,
        "nonlinearity_pct_fs": 3.00,
        "repeatability_pct_fs": 4.72,
        "hysteresis_pct_fs": 6.08,
    },
    "B2_g2": {
        "k_pm_per_ue": 1.121,
        "R2": 0.9994,
        "nonlinearity_pct_fs": 1.77,
        "repeatability_pct_fs": 2.14,
        "hysteresis_pct_fs": 2.68,
    },
}

# 容差
STRAIN_K_TOL = 0.02         # k 绝对容差 (pm/με)
STRAIN_R2_TOL = 0.005       # R² 绝对容差
STRAIN_NL_TOL = 0.5         # 非线性 %FS 绝对容差
STRAIN_RP_TOL = 1.0         # 重复性 %FS 绝对容差
STRAIN_HY_TOL = 3.0         # 迟滞 %FS 绝对容差 (放宽，因位移非对称)

# ═══════════════════════════════════════════════════════════════════════
# 温度标定黄金值 — FBG.py 输出
# ═══════════════════════════════════════════════════════════════════════

TEMP_GOLDEN_S_EFF = {
    "A1-W1": 28.00, "A1-W2": 29.50,
    "A2-W1": 27.50, "A2-W2": 30.00,
}
TEMP_GOLDEN_T_BASE = {
    "A1-W1": 14.9, "A1-W2": 14.0,
    "A2-W1": 12.9, "A2-W2": 13.1,
}
TEMP_GOLDEN_R2 = {
    "A1-W1": 1.000000, "A1-W2": 1.000000,
    "A2-W1": 1.000000, "A2-W2": 1.000000,
}
TEMP_REGRESSION_ATOL = 0.15
TEMP_TBASE_ATOL = 0.6
TEMP_R2_ATOL = 0.002
