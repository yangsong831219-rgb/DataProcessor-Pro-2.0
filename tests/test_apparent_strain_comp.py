"""表观应变-温度查表补偿模块 — 合成数据测试 (standalone, no pytest dependency)

测试:
1. LUT 往返: 已知线性曲线 ε_app(T) 建表 → 查表补偿后应变归零
2. 越界钳位: T 超出 [T_min, T_max] → oob_mask 置位 + 钳位不崩溃
3. T_base 零点: eps_app(T_base) ≈ 0
4. 低通滤波: T 噪声引入的抖动被移动平均压制
5. 边界/异常: 数据点不足、dwell_mask 全 False、eps_dec/T 长度不匹配

用法: python tests/test_apparent_strain_comp.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from utils.apparent_strain_comp import (
    ApparentStrainLUT,
    build_apparent_strain_lut,
    apply_apparent_strain_comp,
)


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed = 0
total = 0

# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def make_linear_lut_data(T_base=25.0, n_pts=500, noise_std=0.5, seed=42):
    """生成线性表观应变曲线: eps_app = 2.0 * (T - T_base) + noise

    即灵敏度 = 2.0 με/°C
    """
    rng = np.random.default_rng(seed)
    T = np.linspace(10.0, 60.0, n_pts) + rng.normal(0, 0.1, n_pts)
    eps_true = 2.0 * (T - T_base)
    eps = eps_true + rng.normal(0, noise_std, n_pts)
    return T, eps


def make_sinusoidal_lut_data(T_base=25.0, n_pts=800, noise_std=0.3, seed=99):
    """生成非线性表观应变曲线: eps_app = 10*sin((T-10)/50 * pi) + 2*(T-T_base)"""
    rng = np.random.default_rng(seed)
    T = np.linspace(10.0, 60.0, n_pts) + rng.normal(0, 0.05, n_pts)
    eps_true = 10.0 * np.sin((T - 10.0) / 50.0 * np.pi) + 2.0 * (T - T_base)
    eps = eps_true + rng.normal(0, noise_std, n_pts)
    return T, eps


# ═══════════════════════════════════════════════════════════════════════
# Test 1: LUT 往返 — 线性曲线
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestLinearRoundtrip ═══")

T_base = 25.0
T_build, eps_build = make_linear_lut_data(T_base=T_base, n_pts=1000, noise_std=0.2, seed=1)
lut = build_apparent_strain_lut(T_build, eps_build, T_base=T_base,
                                 bin_width=2.0, min_count=20, sensor="test1",
                                 n_cycles=3, source="synthetic")

total += 1; passed += check("LUT has >=2 points", len(lut.T_grid) >= 2)
total += 1; passed += check("T_base stored", lut.T_base == T_base)
total += 1; passed += check("sensor stored", lut.sensor == "test1")
total += 1; passed += check("n_cycles stored", lut.n_cycles == 3)

# 查表补偿 — 用建表同温度范围
T_test = np.linspace(12.0, 55.0, 200)
eps_true = 2.0 * (T_test - T_base)  # 真实表观应变
eps_dec = eps_true + 50.0  # 模拟解耦应变 (含 50με 偏置)
eps_corr, oob_mask = apply_apparent_strain_comp(eps_dec, T_test, lut)

total += 1; passed += check("eps_corr shape matches", len(eps_corr) == len(T_test))
total += 1; passed += check("oob_mask shape matches", len(oob_mask) == len(T_test))
total += 1; passed += check("no oob in valid range", not np.any(oob_mask))

# 补偿后应变应接近 50με (偏置被保留，表观应变被扣除)
residual = eps_corr - 50.0
rmse = np.sqrt(np.mean(residual ** 2))
total += 1; passed += check(f"comp residual RMSE < 2.0 με (got {rmse:.2f})", rmse < 2.0)

# ═══════════════════════════════════════════════════════════════════════
# Test 2: T_base 零点
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestTbaseZero ═══")

T_base2 = 30.0
T_b2, eps_b2 = make_linear_lut_data(T_base=T_base2, n_pts=500, noise_std=0.1, seed=7)
lut2 = build_apparent_strain_lut(T_b2, eps_b2, T_base=T_base2,
                                  bin_width=2.0, min_count=8)

# 查表在 T_base 处应返回 ~0
T_at_base = np.array([T_base2, T_base2, T_base2])
eps_at_base = np.array([0.0, 0.0, 0.0])
eps_c, oob = apply_apparent_strain_comp(eps_at_base, T_at_base, lut2)
total += 1; passed += check(f"eps_app(T_base)≈0: {eps_c[0]:.3f}", abs(eps_c[0]) < 1.0)
total += 1; passed += check("no oob at T_base", not np.any(oob))

# ═══════════════════════════════════════════════════════════════════════
# Test 3: 越界钳位 + oob_mask
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestOutOfBounds ═══")

T_base3 = 25.0
T_b3, eps_b3 = make_linear_lut_data(T_base=T_base3, n_pts=500, noise_std=0.2, seed=3)
lut3 = build_apparent_strain_lut(T_b3, eps_b3, T_base=T_base3,
                                  bin_width=2.0, min_count=20)

# 构造越界温度: T 从 0→80°C，但 LUT 只覆盖 ~10-60°C
T_oob = np.array([0.0, 5.0, T_base3, 65.0, 75.0, 80.0])
eps_oob = np.zeros_like(T_oob)
eps_c3, oob3 = apply_apparent_strain_comp(eps_oob, T_oob, lut3)

total += 1; passed += check("oob low detected (T=0)", oob3[0])
total += 1; passed += check("oob low detected (T=5)", oob3[1])
total += 1; passed += check("in-range not oob (T_base)", not oob3[2])
total += 1; passed += check("oob high detected (T=65)", oob3[3])
total += 1; passed += check("oob high detected (T=75)", oob3[4])
total += 1; passed += check("oob high detected (T=80)", oob3[5])

# 钳位不崩溃: 越界点返回有限值 (非 NaN 非 inf)
total += 1; passed += check("all eps_corr finite", np.all(np.isfinite(eps_c3)))

# 越界点补偿值应等于端点补偿值 (钳位，非外推)
eps_at_low_end = np.array([0.0])
eps_at_high_end = np.array([0.0])
T_low_clamp = np.array([lut3.T_min])
T_high_clamp = np.array([lut3.T_max])
low_c, _ = apply_apparent_strain_comp(eps_at_low_end, T_low_clamp, lut3)
high_c, _ = apply_apparent_strain_comp(eps_at_high_end, T_high_clamp, lut3)

# T=0 越界 → 钳位到 T_min 的补偿值
total += 1; passed += check(
    f"oob T=0 clamped to T_min value (got {eps_c3[0]:.4f} vs {low_c[0]:.4f})",
    abs(eps_c3[0] - low_c[0]) < 1e-9
)
total += 1; passed += check(
    f"oob T=80 clamped to T_max value (got {eps_c3[5]:.4f} vs {high_c[0]:.4f})",
    abs(eps_c3[5] - high_c[0]) < 1e-9
)

# ═══════════════════════════════════════════════════════════════════════
# Test 4: 低通滤波压制 T 噪声
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestLowpass ═══")

T_base4 = 25.0
rng = np.random.default_rng(42)
n4 = 500
# 恒定温度 + 高频噪声 (模拟恒温段 ADC 抖动)
T_clean = np.full(n4, 35.0)
T_noisy = T_clean + rng.normal(0, 2.0, n4)  # σ=2°C 高频抖动
eps_true4 = 2.0 * (T_clean - T_base4)
eps_dec4 = eps_true4 + 50.0  # 50με 偏置

# LUT 覆盖 10-60°C 范围
T_lut = np.linspace(10.0, 60.0, 2000) + rng.normal(0, 0.05, 2000)
eps_lut = 2.0 * (T_lut - T_base4)
lut4 = build_apparent_strain_lut(T_lut, eps_lut, T_base=T_base4,
                                  bin_width=2.0, min_count=20)

# 无低通: T 噪声 → 补偿值抖动
eps_raw, oob_raw = apply_apparent_strain_comp(eps_dec4, T_noisy, lut4)
residual_raw = eps_raw - 50.0
std_raw = float(np.std(residual_raw))

# 有低通 (窗口=31): 平稳信号上 MA 无偏且降噪
eps_lp, oob_lp = apply_apparent_strain_comp(eps_dec4, T_noisy, lut4,
                                             t_lowpass_win=31)
residual_lp = eps_lp - 50.0
std_lp = float(np.std(residual_lp))

# 修剪边界 (mode='same' 零填充导致边缘偏差)，比较内部区域
trim = 30  # 去掉首尾各 30 点
residual_raw_trim = residual_raw[trim:-trim]
residual_lp_trim = residual_lp[trim:-trim]
std_raw_trim = float(np.std(residual_raw_trim))
std_lp_trim = float(np.std(residual_lp_trim))

total += 1; passed += check(
    f"lowpass reduces noise std (trimmed, LP={std_lp_trim:.3f} < Raw={std_raw_trim:.3f})",
    std_lp_trim < std_raw_trim
)

# 低通输出全部有限、长度正确
total += 1; passed += check("lowpass output all finite", np.all(np.isfinite(eps_lp)))
total += 1; passed += check("lowpass output length correct", len(eps_lp) == n4)

# ═══════════════════════════════════════════════════════════════════════
# Test 5: 非线性曲线往返 (二次型，模拟真实表观应变)
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestNonlinearRoundtrip ═══")

T_base5 = 25.0
rng5 = np.random.default_rng(12)
n5 = 2000
# 二次型表观应变: eps = 0.02*(T-25)^2 + 1.5*(T-25) — 真实传感器典型非线性
T_b5 = np.linspace(10.0, 60.0, n5) + rng5.normal(0, 0.05, n5)
eps_true5 = 0.02 * (T_b5 - T_base5)**2 + 1.5 * (T_b5 - T_base5)
eps_b5 = eps_true5 + rng5.normal(0, 0.15, n5)

lut5 = build_apparent_strain_lut(T_b5, eps_b5, T_base=T_base5,
                                  bin_width=1.5, min_count=20, sensor="quad_test")

# 补偿: 解耦应变 = 真实表观应变 + 100με 信号
T_test5 = np.linspace(12.0, 58.0, 400)
eps_signal = 100.0 * np.ones_like(T_test5)
eps_true5_test = 0.02 * (T_test5 - T_base5)**2 + 1.5 * (T_test5 - T_base5)
eps_dec5 = eps_true5_test + eps_signal

eps_c5, oob5 = apply_apparent_strain_comp(eps_dec5, T_test5, lut5)
residual5 = eps_c5 - eps_signal
rmse5 = np.sqrt(np.mean(residual5 ** 2))
# 二次型分箱+bilinear插值残差 < 2.0 με (bin_width=1.5°C)
total += 1; passed += check(f"quadratic roundtrip RMSE < 2.0 με (got {rmse5:.2f})", rmse5 < 2.0)
total += 1; passed += check("no oob in quadratic range", not np.any(oob5))

# ═══════════════════════════════════════════════════════════════════════
# Test 6: dwell_mask 选样模式
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestDwellMask ═══")

T_base6 = 25.0
n6 = 600
rng6 = np.random.default_rng(555)
# 台阶实验: 5 个温度平台，每个平台后 50 点为稳定尾段
T_plat = np.concatenate([
    np.full(100, 15.0),
    np.full(100, 25.0),
    np.full(100, 35.0),
    np.full(100, 45.0),
    np.full(100, 55.0),
    np.full(100, 60.0),
])
T_all = T_plat + rng6.normal(0, 0.3, n6)
eps_true6 = 1.8 * (T_all - T_base6)
eps_all = eps_true6 + rng6.normal(0, 0.5, n6)

# dwell_mask: 只取每个平台的最后 30 点 (稳定尾段)
dwell = np.zeros(n6, dtype=bool)
for plat_start in range(0, n6, 100):
    dwell[plat_start + 70:plat_start + 100] = True

lut6_full = build_apparent_strain_lut(T_all, eps_all, T_base=T_base6,
                                       bin_width=2.0, min_count=20, source="full")
lut6_dwell = build_apparent_strain_lut(T_all, eps_all, T_base=T_base6,
                                        bin_width=2.0, min_count=10,
                                        dwell_mask=dwell, source="dwell_tail")

total += 1; passed += check("dwell LUT has >=2 points", len(lut6_dwell.T_grid) >= 2)
total += 1; passed += check("dwell LUT source tag", lut6_dwell.source == "dwell_tail")
total += 1; passed += check("full LUT source tag", lut6_full.source == "full")

# dwell 建表点应少于 full (只用尾段)
total += 1; passed += check(
    f"dwell uses fewer bins than full ({len(lut6_dwell.T_grid)} vs {len(lut6_full.T_grid)})",
    len(lut6_dwell.T_grid) <= len(lut6_full.T_grid)
)

# ═══════════════════════════════════════════════════════════════════════
# Test 7: 边界/异常
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ TestEdgeCases ═══")

# 7a: 数据点不足
try:
    build_apparent_strain_lut(np.array([1.0, 2.0]), np.array([0.0, 0.1]),
                               T_base=25.0, min_count=50)
    total += 1; passed += check("min_count insufficient → ValueError", False)
except ValueError as e:
    total += 1; passed += check(f"min_count insufficient → ValueError: {str(e)[:60]}", True)

# 7b: dwell_mask 全 False
try:
    build_apparent_strain_lut(np.linspace(10, 60, 100), np.zeros(100),
                               T_base=25.0, dwell_mask=np.zeros(100, dtype=bool))
    total += 1; passed += check("dwell_mask all False → ValueError", False)
except ValueError as e:
    total += 1; passed += check(f"dwell_mask all False → ValueError: {str(e)[:60]}", True)

# 7c: eps_dec / T 长度不匹配
lut7 = build_apparent_strain_lut(
    np.linspace(10, 60, 500), np.linspace(-10, 10, 500),
    T_base=25.0, bin_width=2.0, min_count=15)
try:
    apply_apparent_strain_comp(np.array([1.0, 2.0]), np.array([20.0]), lut7)
    total += 1; passed += check("length mismatch → ValueError", False)
except ValueError as e:
    total += 1; passed += check(f"length mismatch → ValueError: {str(e)[:60]}", True)

# 7d: from_dict / to_dict roundtrip
lut7_dict = lut7.to_dict()
lut7_rt = ApparentStrainLUT.from_dict(lut7_dict)
total += 1; passed += check("to_dict/from_dict: sensor", lut7_rt.sensor == lut7.sensor)
total += 1; passed += check("to_dict/from_dict: T_base", lut7_rt.T_base == lut7.T_base)
total += 1; passed += check("to_dict/from_dict: T_grid length", len(lut7_rt.T_grid) == len(lut7.T_grid))
total += 1; passed += check("to_dict/from_dict: eps_app length", len(lut7_rt.eps_app) == len(lut7.eps_app))

# 7e: 偶数低通窗口自动取奇
T_test7 = np.linspace(20, 50, 100)
eps_test7 = np.zeros(100)
eps_c_even, oob_even = apply_apparent_strain_comp(eps_test7, T_test7, lut7,
                                                   t_lowpass_win=10)  # 偶数→自动+1=11
total += 1; passed += check("even lowpass window doesn't crash", np.all(np.isfinite(eps_c_even)))

# ═══════════════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  {passed}/{total} PASSED")
if passed == total:
    print("  [OK] ALL TESTS PASSED")
else:
    print(f"  [FAIL] {total - passed} FAILURES")
print(f"{'='*50}")

# 直接用于 pyright 调用后返回 exit code
if __name__ == "__main__":
    import sys as _sys
    _sys.exit(0 if passed == total else 1)
