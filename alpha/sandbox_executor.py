"""
安全沙箱: 执行 Kimi 生成的 Python 代码

设计原则:
  - Kimi 生成的代码运行在受限 exec() 环境中
  - 只允许 numpy / pandas / math / statistics
  - 禁止所有 I/O: 文件、网络、系统调用
  - 60 秒超时 (Windows 用 threading.Timer)
  - 静态分析 + 运行时双重防护

用法:
  sandbox = SandboxExecutor()
  mask, stats = sandbox.execute_entry_detector(code, df)
  exit_info = sandbox.execute_exit_miner(code, df, positions, "long", close)
"""

from __future__ import annotations

import logging
import math
import re
import statistics
import threading
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── 异常类 ────────────────────────────────────────────────────────────────────

class CodeValidationError(Exception):
    """Kimi 代码未通过静态安全检查。"""


class ExecutionTimeoutError(Exception):
    """Kimi 代码执行超时。"""


class ExecutionRuntimeError(Exception):
    """Kimi 代码运行时错误。"""


# ── 执行结果 ──────────────────────────────────────────────────────────────────

@dataclass
class EntryResult:
    """入场检测代码执行结果。"""
    mask: pd.Series          # bool Series, True = 入场信号
    trigger_count: int       # 触发次数
    trigger_rate: float      # 触发率 (%)
    error: str = ""          # 错误信息 (空 = 成功)


@dataclass
class ExitResult:
    """出场挖掘代码执行结果。"""
    exit_info: dict          # 出场条件 dict (top3 格式)
    error: str = ""


# ── 沙箱执行器 ────────────────────────────────────────────────────────────────

class SandboxExecutor:
    """
    安全执行 Kimi 生成的 Python 代码。

    安全层:
      1. 静态分析: 正则检查禁止模式 (import, open, exec, eval, os, sys, ...)
      2. 受限 globals: 只暴露白名单内置函数 + numpy + pandas + math
      3. 超时: 60 秒 (Windows threading.Timer)
      4. 异常捕获: 所有运行时错误被包装返回, 不会泄露到调用方
    """

    TIMEOUT_SECONDS = 60

    # 白名单内置函数
    # 注意: chr/bytes/type 已移除 (Codex 审计: H-1 字符串拼接绕过, H-3 type 探测)
    ALLOWED_BUILTINS = {
        "abs", "all", "any", "bool", "complex",
        "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "hash", "int",
        "isinstance", "issubclass", "iter", "len", "list", "map",
        "max", "min", "next", "pow", "print", "range",
        "repr", "reversed", "round", "set", "slice", "sorted",
        "str", "sum", "tuple", "zip",
        # 常量
        "True", "False", "None",
        "StopIteration", "ValueError", "TypeError", "KeyError",
        "IndexError", "ZeroDivisionError", "RuntimeError",
        "Exception",
    }

    # 禁止模式 (正则) -- 只拦截真正危险的代码结构，不误伤变量名和注释
    # 安全策略: 静态拦 import/dunder + 运行时靠受限 builtins 阻止其他
    FORBIDDEN_PATTERNS = [
        # 核心: import 语句 (唯一不能靠运行时拦的)
        (r"^\s*import\s+", "禁止 import 语句"),
        (r"^\s*from\s+\w+\s+import", "禁止 from...import 语句"),
        (r"\b__import__\s*\(", "禁止 __import__()"),
        # dunder 逃逸 (Codex 审计 C-2/C-3)
        (r"__dict__", "禁止 __dict__"),
        (r"__globals__", "禁止 __globals__"),
        (r"__builtins__", "禁止 __builtins__"),
        (r"__class__", "禁止 __class__"),
        (r"__subclasses__", "禁止 __subclasses__"),
        (r"__bases__", "禁止 __bases__"),
        (r"__mro__", "禁止 __mro__"),
        (r"__loader__", "禁止 __loader__"),
        (r"__spec__", "禁止 __spec__"),
        (r"__code__", "禁止 __code__"),
        (r"__closure__", "禁止 __closure__"),
        # pandas IO (Codex 审计 C-1)
        (r"\.read_csv\s*\(", "禁止 read_csv"),
        (r"\.read_parquet\s*\(", "禁止 read_parquet"),
        (r"\.read_excel\s*\(", "禁止 read_excel"),
        (r"\.read_json\s*\(", "禁止 read_json"),
        (r"\.to_csv\s*\(", "禁止 to_csv"),
        (r"\.to_parquet\s*\(", "禁止 to_parquet"),
        (r"\.to_excel\s*\(", "禁止 to_excel"),
        (r"\.to_pickle\s*\(", "禁止 to_pickle"),
        # 危险内置函数调用
        (r"\bexec\s*\(", "禁止 exec()"),
        (r"\beval\s*\(", "禁止 eval()"),
        (r"\bcompile\s*\(", "禁止 compile()"),
        (r"\bgetattr\s*\(", "禁止 getattr()"),
        (r"\bopen\s*\(", "禁止 open()"),
    ]

    # ── 静态代码检查 ──────────────────────────────────────────────────────────

    def validate_code(self, code: str) -> list[str]:
        """
        静态分析 Kimi 生成的代码, 返回违规列表。
        空列表 = 安全。

        注意: 只检查代码行（跳过注释），用 MULTILINE 匹配行首 import。
        """
        violations = []
        # 先去除注释内容，避免注释中的关键字误拦
        cleaned_lines = []
        for line in code.split("\n"):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # 跳过纯注释行
            # 去除行内注释
            comment_pos = line.find("#")
            if comment_pos >= 0:
                # 检查 # 是否在字符串内（简化处理：不在引号内就算注释）
                before = line[:comment_pos]
                if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                    line = before
            cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines)

        for pattern, desc in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, cleaned, re.MULTILINE):
                violations.append(desc)

        return violations

    # ── 构建受限 namespace ────────────────────────────────────────────────────

    def _build_restricted_globals(
        self,
        extra_context: Optional[dict] = None,
    ) -> dict:
        """
        构建受限的执行命名空间。

        只包含:
          - 白名单内置函数
          - numpy (as np)
          - pandas (as pd)
          - math
          - statistics
          - 额外上下文变量 (df, entry_positions 等)
        """
        # 构建受限 builtins
        import builtins
        safe_builtins = {}
        for name in self.ALLOWED_BUILTINS:
            if hasattr(builtins, name):
                safe_builtins[name] = getattr(builtins, name)

        namespace = {
            "__builtins__": safe_builtins,
            "np": np,
            "pd": pd,
            "math": math,
            "statistics": statistics,
            # numpy 常用子模块
            "nan": np.nan,
            "inf": np.inf,
        }

        if extra_context:
            namespace.update(extra_context)

        return namespace

    # ── 超时执行 ──────────────────────────────────────────────────────────────

    def _execute_with_timeout(
        self,
        code: str,
        namespace: dict,
        timeout: int = 0,
    ) -> tuple[dict, Optional[str]]:
        """
        在超时保护下执行代码。

        Returns:
            (namespace, error_msg)
            namespace: 执行后的命名空间 (含函数定义等)
            error_msg: None = 成功, 非 None = 错误描述
        """
        timeout = timeout or self.TIMEOUT_SECONDS
        result_box: list = [None]  # [error_msg]

        def _run():
            try:
                exec(code, namespace)  # noqa: S102
                result_box[0] = None
            except Exception as exc:
                result_box[0] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            # 超时 - daemon thread 会在主线程结束后被回收
            return namespace, f"执行超时 (>{timeout}s)"

        return namespace, result_box[0]

    # ── 入场检测代码执行 ──────────────────────────────────────────────────────

    def execute_entry_detector(
        self,
        code: str,
        df: pd.DataFrame,
    ) -> EntryResult:
        """
        执行 Kimi 写的入场检测代码。

        代码必须定义: def detect_entry(df: pd.DataFrame) -> pd.Series
        返回 bool Series, True = 入场信号。

        Args:
            code: Kimi 生成的 Python 代码字符串
            df: 含所有特征的 DataFrame

        Returns:
            EntryResult
        """
        # 1. 静态检查
        violations = self.validate_code(code)
        if violations:
            return EntryResult(
                mask=pd.Series(False, index=df.index),
                trigger_count=0,
                trigger_rate=0.0,
                error=f"代码安全检查失败: {'; '.join(violations)}",
            )

        # 2. 构建受限环境
        namespace = self._build_restricted_globals({"df": df.copy()})

        # 3. 执行代码 (定义函数)
        namespace_out, exec_error = self._execute_with_timeout(code, namespace)
        if exec_error:
            return EntryResult(
                mask=pd.Series(False, index=df.index),
                trigger_count=0,
                trigger_rate=0.0,
                error=f"代码定义阶段错误: {exec_error}",
            )

        # 4. 调用 detect_entry(df)
        detect_fn = namespace_out.get("detect_entry")
        if not callable(detect_fn):
            return EntryResult(
                mask=pd.Series(False, index=df.index),
                trigger_count=0,
                trigger_rate=0.0,
                error="代码未定义 detect_entry(df) 函数",
            )

        call_ns = {"_fn": detect_fn, "_df": df.copy()}
        call_code = "_result = _fn(_df)"
        call_ns_out, call_error = self._execute_with_timeout(
            call_code,
            {**self._build_restricted_globals(), **call_ns},
        )
        if call_error:
            return EntryResult(
                mask=pd.Series(False, index=df.index),
                trigger_count=0,
                trigger_rate=0.0,
                error=f"detect_entry() 执行错误: {call_error}",
            )

        result = call_ns_out.get("_result")

        # 5. 验证返回值
        if result is None:
            return EntryResult(
                mask=pd.Series(False, index=df.index),
                trigger_count=0,
                trigger_rate=0.0,
                error="detect_entry() 返回 None",
            )

        if isinstance(result, np.ndarray):
            result = pd.Series(result, index=df.index)
        elif not isinstance(result, pd.Series):
            return EntryResult(
                mask=pd.Series(False, index=df.index),
                trigger_count=0,
                trigger_rate=0.0,
                error=f"detect_entry() 返回类型错误: {type(result).__name__}, 应为 pd.Series",
            )

        # 强制转 bool
        mask = result.astype(bool).reindex(df.index, fill_value=False)
        trigger_count = int(mask.sum())
        trigger_rate = trigger_count / max(len(df), 1) * 100.0

        logger.info(
            "[SANDBOX] 入场检测: 触发 %d 次 (%.2f%%)",
            trigger_count, trigger_rate,
        )

        return EntryResult(
            mask=mask,
            trigger_count=trigger_count,
            trigger_rate=round(trigger_rate, 4),
        )

    # ── 出场挖掘代码执行 ──────────────────────────────────────────────────────

    def execute_exit_miner(
        self,
        code: str,
        df: pd.DataFrame,
        entry_positions: list[int],
        direction: str,
        close: np.ndarray,
    ) -> ExitResult:
        """
        执行 Kimi 写的出场条件挖掘代码。

        代码必须定义:
          def mine_exit_conditions(
              df: pd.DataFrame,
              entry_positions: list[int],
              direction: str,
              close: np.ndarray,
          ) -> dict

        返回 dict 包含 top3 出场条件。

        Args:
            code: Kimi 生成的 Python 代码字符串
            df: 含所有特征的 DataFrame
            entry_positions: 入场 bar 的整数位置列表
            direction: "long" 或 "short"
            close: 收盘价 numpy 数组

        Returns:
            ExitResult
        """
        # 1. 静态检查
        violations = self.validate_code(code)
        if violations:
            return ExitResult(
                exit_info={},
                error=f"代码安全检查失败: {'; '.join(violations)}",
            )

        # 2. 构建受限环境
        namespace = self._build_restricted_globals({
            "df": df.copy(),
            "entry_positions": list(entry_positions),
            "direction": str(direction),
            "close": close.copy(),
        })

        # 3. 执行代码 (定义函数)
        namespace_out, exec_error = self._execute_with_timeout(code, namespace)
        if exec_error:
            return ExitResult(
                exit_info={},
                error=f"代码定义阶段错误: {exec_error}",
            )

        # 4. 调用 mine_exit_conditions(...)
        mine_fn = namespace_out.get("mine_exit_conditions")
        if not callable(mine_fn):
            return ExitResult(
                exit_info={},
                error="代码未定义 mine_exit_conditions() 函数",
            )

        call_ns = {
            "_fn": mine_fn,
            "_df": df.copy(),
            "_positions": list(entry_positions),
            "_direction": str(direction),
            "_close": close.copy(),
        }
        call_code = "_result = _fn(_df, _positions, _direction, _close)"
        call_ns_out, call_error = self._execute_with_timeout(
            call_code,
            {**self._build_restricted_globals(), **call_ns},
        )
        if call_error:
            return ExitResult(
                exit_info={},
                error=f"mine_exit_conditions() 执行错误: {call_error}",
            )

        result = call_ns_out.get("_result")

        # 5. 验证返回值
        if not isinstance(result, dict):
            return ExitResult(
                exit_info={},
                error=f"mine_exit_conditions() 返回类型错误: {type(result).__name__}, 应为 dict",
            )

        top3 = result.get("top3")
        if not isinstance(top3, list) or len(top3) == 0:
            return ExitResult(
                exit_info={},
                error="mine_exit_conditions() 返回的 dict 缺少 top3 或为空",
            )

        # 验证每个 combo 的结构
        for i, combo in enumerate(top3):
            if not isinstance(combo, dict):
                return ExitResult(
                    exit_info={},
                    error=f"top3[{i}] 不是 dict",
                )
            conditions = combo.get("conditions")
            if not isinstance(conditions, list) or len(conditions) == 0:
                return ExitResult(
                    exit_info={},
                    error=f"top3[{i}].conditions 缺失或为空",
                )
            for j, cond in enumerate(conditions):
                if not isinstance(cond, dict):
                    return ExitResult(
                        exit_info={},
                        error=f"top3[{i}].conditions[{j}] 不是 dict",
                    )
                for key in ("feature", "operator", "threshold"):
                    if key not in cond:
                        return ExitResult(
                            exit_info={},
                            error=f"top3[{i}].conditions[{j}] 缺少 '{key}'",
                        )
                if cond["operator"] not in ("<", ">"):
                    return ExitResult(
                        exit_info={},
                        error=f"top3[{i}].conditions[{j}].operator='{cond['operator']}', 只允许 '<' 或 '>'",
                    )

        logger.info(
            "[SANDBOX] 出场挖掘: 返回 %d 个 combo",
            len(top3),
        )

        return ExitResult(exit_info=result)

    # ── 通用代码执行 ──────────────────────────────────────────────────────────

    def execute_generic(
        self,
        code: str,
        context: dict,
        result_var: str = "_result",
    ) -> tuple[Any, str]:
        """
        执行任意分析代码, 从命名空间取出指定变量。

        Args:
            code: Python 代码
            context: 注入到命名空间的变量
            result_var: 从命名空间取回的变量名

        Returns:
            (result, error_msg)  error_msg 为空字符串表示成功
        """
        violations = self.validate_code(code)
        if violations:
            return None, f"安全检查失败: {'; '.join(violations)}"

        namespace = self._build_restricted_globals(context)
        namespace_out, exec_error = self._execute_with_timeout(code, namespace)
        if exec_error:
            return None, exec_error

        result = namespace_out.get(result_var)
        return result, ""
