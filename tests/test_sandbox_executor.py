"""
sandbox_executor.py 安全性 + 功能测试

测试覆盖:
  1. 静态代码检查 (禁止模式检测)
  2. 受限环境 (无法访问 os/sys/文件)
  3. 超时保护 (死循环代码被杀)
  4. 正确执行入场检测代码
  5. 正确执行出场挖掘代码
  6. 返回值格式验证
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

import numpy as np
import pandas as pd
import pytest

from alpha.sandbox_executor import (
    SandboxExecutor,
    CodeValidationError,
    EntryResult,
    ExitResult,
)


@pytest.fixture
def sandbox():
    return SandboxExecutor()


@pytest.fixture
def sample_df():
    """构建一个小型测试 DataFrame。"""
    n = 500
    np.random.seed(42)
    return pd.DataFrame({
        "timestamp": np.arange(n) * 60000,
        "close": 80000 + np.cumsum(np.random.randn(n) * 10),
        "high": 80000 + np.cumsum(np.random.randn(n) * 10) + 5,
        "low": 80000 + np.cumsum(np.random.randn(n) * 10) - 5,
        "volume": np.random.uniform(1, 100, n),
        "vwap_deviation": np.random.randn(n) * 0.02,
        "position_in_range_24h": np.random.uniform(0, 1, n),
        "taker_buy_sell_ratio": np.random.uniform(0.5, 1.5, n),
        "volume_vs_ma20": np.random.uniform(0.5, 2.0, n),
        "oi_change_rate_5m": np.random.randn(n) * 0.01,
    })


# ── 1. 静态安全检查 ──────────────────────────────────────────────────────────


class TestCodeValidation:
    def test_import_blocked(self, sandbox):
        code = "import os\nos.system('rm -rf /')"
        violations = sandbox.validate_code(code)
        assert len(violations) > 0
        assert any("import" in v for v in violations)

    def test_open_blocked(self, sandbox):
        code = "f = open('/etc/passwd', 'r')"
        violations = sandbox.validate_code(code)
        assert any("open" in v for v in violations)

    def test_exec_eval_blocked(self, sandbox):
        code = "exec('print(1)')\neval('1+1')"
        violations = sandbox.validate_code(code)
        assert len(violations) >= 2

    def test_dunder_blocked(self, sandbox):
        code = "x = ().__class__.__bases__[0].__subclasses__()"
        violations = sandbox.validate_code(code)
        assert len(violations) > 0

    def test_os_sys_blocked(self, sandbox):
        code = "os.path.exists('/tmp')\nsys.exit(0)"
        violations = sandbox.validate_code(code)
        assert len(violations) >= 2

    def test_network_blocked(self, sandbox):
        code = "import requests\nimport socket\nimport urllib"
        violations = sandbox.validate_code(code)
        assert len(violations) >= 3

    def test_safe_code_passes(self, sandbox):
        code = """
def detect_entry(df):
    mask = df["vwap_deviation"] < -0.02
    return mask
"""
        violations = sandbox.validate_code(code)
        assert violations == []

    def test_numpy_pandas_allowed(self, sandbox):
        code = """
def detect_entry(df):
    arr = np.array([1, 2, 3])
    s = pd.Series(arr)
    return s > 1
"""
        violations = sandbox.validate_code(code)
        assert violations == []

    # ── Codex 审计攻击向量 ────────────────────────────────────────────────

    def test_pandas_read_csv_blocked(self, sandbox):
        """C-1: pd.read_csv 文件读取绕过。"""
        code = 'secrets = pd.read_csv("C:/Users/.env")'
        violations = sandbox.validate_code(code)
        assert any("pandas" in v.lower() or "read" in v.lower() for v in violations)

    def test_pandas_to_csv_blocked(self, sandbox):
        """C-1: DataFrame.to_csv 文件写入绕过。"""
        code = 'pd.DataFrame({"a": [1]}).to_csv("hack.csv")'
        violations = sandbox.validate_code(code)
        assert any("to_csv" in v for v in violations)

    def test_dunder_dict_blocked(self, sandbox):
        """C-2: np.__dict__ 字符串拼接访问真实 builtins。"""
        code = 'x = np.__dict__["__buil" + "tins__"]'
        violations = sandbox.validate_code(code)
        assert any("__dict__" in v for v in violations)

    def test_dunder_globals_blocked(self, sandbox):
        """C-3: detect_entry.__globals__ 泄漏。"""
        code = """
def detect_entry(df):
    g = detect_entry.__globals__
    return df["close"] > 0
"""
        violations = sandbox.validate_code(code)
        assert any("__globals__" in v for v in violations)

    def test_chr_blocked(self, sandbox):
        """H-1: chr() 字符串拼接绕过。"""
        code = 'evil = "".join(map(chr, [111, 115]))'
        violations = sandbox.validate_code(code)
        assert any("chr" in v for v in violations)

    def test_getattr_blocked(self, sandbox):
        """H-3: getattr 绕过属性访问限制。"""
        code = 'x = getattr(np, "__dict__")'
        violations = sandbox.validate_code(code)
        assert len(violations) > 0

    def test_bytes_blocked(self, sandbox):
        """H-1: bytes() 构造绕过。"""
        code = 'evil = bytes([111, 115]).decode()'
        violations = sandbox.validate_code(code)
        assert any("bytes" in v for v in violations)


# ── 2. 入场检测执行 ──────────────────────────────────────────────────────────


class TestEntryDetector:
    def test_simple_threshold(self, sandbox, sample_df):
        code = """
def detect_entry(df):
    return df["vwap_deviation"] < -0.03
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert result.error == ""
        assert result.trigger_count > 0
        assert result.trigger_rate > 0
        assert len(result.mask) == len(sample_df)
        assert result.mask.dtype == bool

    def test_multi_condition(self, sandbox, sample_df):
        code = """
def detect_entry(df):
    cond1 = df["vwap_deviation"] < -0.02
    cond2 = df["volume_vs_ma20"] < 0.8
    return cond1 & cond2
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert result.error == ""
        assert result.trigger_count >= 0

    def test_numpy_operations(self, sandbox, sample_df):
        code = """
def detect_entry(df):
    vwap = df["vwap_deviation"].values
    rolling_min = pd.Series(vwap).rolling(20).min().values
    mask = (vwap < -0.02) & (vwap <= rolling_min * 1.1)
    return pd.Series(mask, index=df.index)
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert result.error == ""

    def test_missing_function(self, sandbox, sample_df):
        code = """
x = 42
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert "detect_entry" in result.error

    def test_wrong_return_type(self, sandbox, sample_df):
        code = """
def detect_entry(df):
    return "not a series"
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert "类型错误" in result.error

    def test_runtime_error_caught(self, sandbox, sample_df):
        code = """
def detect_entry(df):
    return df["nonexistent_column"] > 0
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert result.error != ""
        assert result.trigger_count == 0

    def test_forbidden_code_rejected(self, sandbox, sample_df):
        code = """
import os
def detect_entry(df):
    os.system("echo hacked")
    return df["vwap_deviation"] < 0
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert "安全检查失败" in result.error

    def test_ndarray_auto_convert(self, sandbox, sample_df):
        """numpy 数组返回值自动转 pd.Series。"""
        code = """
def detect_entry(df):
    return np.array(df["vwap_deviation"].values < -0.02)
"""
        result = sandbox.execute_entry_detector(code, sample_df)
        assert result.error == ""
        assert isinstance(result.mask, pd.Series)


# ── 3. 出场挖掘执行 ──────────────────────────────────────────────────────────


class TestExitMiner:
    def test_simple_exit_miner(self, sandbox, sample_df):
        code = """
def mine_exit_conditions(df, entry_positions, direction, close):
    sign = -1.0 if direction == "short" else 1.0
    return {
        "top3": [
            {
                "conditions": [
                    {"feature": "vwap_deviation_vs_entry", "operator": ">", "threshold": 0.01}
                ],
                "combo_label": "C1",
                "description": "VWAP normalized"
            }
        ],
        "stop_grid": {"min": 0.3, "max": 1.0, "step": 0.1}
    }
"""
        positions = [10, 50, 100, 200]
        close = sample_df["close"].values
        result = sandbox.execute_exit_miner(code, sample_df, positions, "long", close)
        assert result.error == ""
        assert "top3" in result.exit_info
        assert len(result.exit_info["top3"]) == 1

    def test_missing_function(self, sandbox, sample_df):
        code = "x = 1"
        result = sandbox.execute_exit_miner(
            code, sample_df, [10], "long", sample_df["close"].values
        )
        assert "mine_exit_conditions" in result.error

    def test_invalid_combo_structure(self, sandbox, sample_df):
        code = """
def mine_exit_conditions(df, entry_positions, direction, close):
    return {"top3": [{"no_conditions": True}]}
"""
        result = sandbox.execute_exit_miner(
            code, sample_df, [10], "long", sample_df["close"].values
        )
        assert "conditions" in result.error

    def test_invalid_operator(self, sandbox, sample_df):
        code = """
def mine_exit_conditions(df, entry_positions, direction, close):
    return {
        "top3": [{
            "conditions": [{"feature": "x", "operator": "==", "threshold": 0.1}],
            "combo_label": "C1"
        }]
    }
"""
        result = sandbox.execute_exit_miner(
            code, sample_df, [10], "long", sample_df["close"].values
        )
        assert "只允许" in result.error


# ── 4. 超时保护 ──────────────────────────────────────────────────────────────


class TestDataIsolation:
    """H-2: 验证沙箱不会污染原始 DataFrame。"""

    def test_df_not_mutated(self, sandbox, sample_df):
        original_close = sample_df["close"].copy()
        code = """
def detect_entry(df):
    df.loc[:, "close"] = 0.0
    return df["vwap_deviation"] < 0
"""
        sandbox.execute_entry_detector(code, sample_df)
        pd.testing.assert_series_equal(sample_df["close"], original_close)

    def test_exit_miner_df_not_mutated(self, sandbox, sample_df):
        original_close = sample_df["close"].copy()
        code = """
def mine_exit_conditions(df, entry_positions, direction, close):
    df.loc[:, "close"] = 0.0
    close[:] = 0.0
    return {
        "top3": [{
            "conditions": [{"feature": "x_vs_entry", "operator": ">", "threshold": 0.01}],
            "combo_label": "C1"
        }]
    }
"""
        close_arr = sample_df["close"].values.copy()
        sandbox.execute_exit_miner(code, sample_df, [10], "long", close_arr)
        pd.testing.assert_series_equal(sample_df["close"], original_close)


class TestTimeout:
    def test_timeout_kills_infinite_loop(self, sandbox, sample_df):
        sandbox_fast = SandboxExecutor()
        sandbox_fast.TIMEOUT_SECONDS = 3  # 缩短超时用于测试

        code = """
def detect_entry(df):
    while True:
        pass
    return df["vwap_deviation"] < 0
"""
        result = sandbox_fast.execute_entry_detector(code, sample_df)
        assert "超时" in result.error


# ── 5. 通用执行 ──────────────────────────────────────────────────────────────


class TestGenericExecution:
    def test_generic_success(self, sandbox):
        code = "_result = sum(range(100))"
        result, error = sandbox.execute_generic(code, {})
        assert error == ""
        assert result == 4950

    def test_generic_with_context(self, sandbox, sample_df):
        code = "_result = len(df)"
        result, error = sandbox.execute_generic(code, {"df": sample_df})
        assert error == ""
        assert result == len(sample_df)

    def test_generic_forbidden(self, sandbox):
        code = "import os\n_result = os.getcwd()"
        result, error = sandbox.execute_generic(code, {})
        assert "安全检查" in error


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
