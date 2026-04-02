"""
令牌桶限流器
用于控制API请求速率，避免超过币安的限流阈值
"""

import asyncio
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    令牌桶算法实现

    算法原理：
    - 桶容量：最多可存放的令牌数
    - 令牌补充速率：每秒补充的令牌数
    - 请求消费：发送请求前消费1个令牌
    - 阻塞策略：令牌不足时阻塞等待
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        safety_margin: float = 0.95
    ):
        """
        初始化令牌桶

        Args:
            capacity: 桶容量（最大令牌数）
            refill_rate: 令牌补充速率（令牌/秒）
            safety_margin: 安全边际，实际使用容量的百分比
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.effective_capacity = int(capacity * safety_margin)
        self.tokens = float(self.effective_capacity)
        self.last_refill_time = time.time()
        self._lock = asyncio.Lock()

        # 统计信息
        self.total_requests = 0
        self.blocked_count = 0

    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        """
        获取令牌（异步）

        Args:
            tokens: 需要获取的令牌数
            timeout: 超时时间（秒），None表示无限等待

        Returns:
            是否成功获取令牌
        """
        async with self._lock:
            # 补充令牌
            await self._refill()

            # 检查是否有足够令牌
            if self.tokens >= tokens:
                self.tokens -= tokens
                self.total_requests += 1
                logger.debug(
                    f"获取令牌成功: {tokens}, 剩余: {self.tokens:.2f}/{self.effective_capacity}"
                )
                return True

            # 令牌不足，需要等待
            self.blocked_count += 1
            tokens_needed = tokens - self.tokens
            wait_time = tokens_needed / self.refill_rate

            logger.debug(
                f"令牌不足，需要等待 {wait_time:.2f}s "
                f"(当前: {self.tokens:.2f}, 需要: {tokens})"
            )

            # 释放锁，等待补充
            # 注意：这里需要在锁外等待
            return await self._wait_and_acquire(tokens, timeout)

    async def _wait_and_acquire(self, tokens: int, timeout: Optional[float]) -> bool:
        """
        等待令牌补充后获取（锁外等待）
        """
        start_time = time.time()

        while True:
            # 计算需要的等待时间
            async with self._lock:
                await self._refill()

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    self.total_requests += 1
                    return True

                tokens_needed = tokens - self.tokens
                wait_time = tokens_needed / self.refill_rate

            # 检查超时
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed + wait_time > timeout:
                    logger.warning(f"获取令牌超时: 等待了 {timeout:.2f}s")
                    return False

            # 等待一段时间后重试
            await asyncio.sleep(min(wait_time, 0.1))

    async def _refill(self):
        """补充令牌（必须持有锁）"""
        now = time.time()
        elapsed = now - self.last_refill_time
        tokens_to_add = elapsed * self.refill_rate

        self.tokens = min(
            self.effective_capacity,
            self.tokens + tokens_to_add
        )
        self.last_refill_time = now

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "capacity": self.capacity,
            "effective_capacity": self.effective_capacity,
            "current_tokens": self.tokens,
            "refill_rate": self.refill_rate,
            "total_requests": self.total_requests,
            "blocked_count": self.blocked_count,
            "blocked_rate": self.blocked_count / max(self.total_requests, 1)
        }


class BinanceRateLimiter:
    """
    币安API专用限流器

    币安限制：
    - 每分钟1200请求权重
    - 所有K线/聚合交易等端点权重=1
    - 每秒最多20个请求
    """

    def __init__(
        self,
        requests_per_minute: int = 1200,
        safety_margin: float = 0.95
    ):
        """
        初始化币安限流器

        Args:
            requests_per_minute: 每分钟请求限制
            safety_margin: 安全边际（使用限制的百分比）
        """
        self.requests_per_second = requests_per_minute / 60
        self.bucket = TokenBucket(
            capacity=requests_per_minute,
            refill_rate=self.requests_per_second,
            safety_margin=safety_margin
        )

        # 从响应头中解析实际消耗的权重
        self.used_weight = 0
        self.weight_limit = requests_per_minute

    async def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        获取请求许可

        Args:
            timeout: 超时时间

        Returns:
            是否成功获取许可
        """
        return await self.bucket.acquire(tokens=1, timeout=timeout)

    def update_from_response(self, headers: dict):
        """
        从响应头更新权重使用情况

        Args:
            headers: aiohttp响应头
        """
        # 币安返回的实际使用权重
        used_weight = headers.get('X-MBX-USED-WEIGHT')
        if used_weight:
            self.used_weight = int(used_weight)

        # 如果接近限制，记录警告
        if self.used_weight > self.weight_limit * 0.9:
            logger.warning(
                f"接近API限流: 已使用 {self.used_weight}/{self.weight_limit}"
            )

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = self.bucket.get_stats()
        stats.update({
            "used_weight": self.used_weight,
            "weight_limit": self.weight_limit,
            "weight_remaining": self.weight_limit - self.used_weight
        })
        return stats

    async def wait_for_reset(self, cooldown_seconds: int = 60):
        """
        等待限流重置（触发429后调用）

        Args:
            cooldown_seconds: 冷却时间（秒）
        """
        logger.warning(f"触发限流，等待 {cooldown_seconds}s 后重试...")
        await asyncio.sleep(cooldown_seconds)

        # 重置令牌桶
        self.bucket.tokens = float(self.bucket.effective_capacity)
        self.bucket.last_refill_time = time.time()
        self.used_weight = 0


# 单例限流器（全局共享）
_limiter: Optional[BinanceRateLimiter] = None


def get_rate_limiter(
    requests_per_minute: int = 1200,
    safety_margin: float = 0.95
) -> BinanceRateLimiter:
    """
    获取全局限流器实例（单例模式）

    Args:
        requests_per_minute: 每分钟请求限制
        safety_margin: 安全边际

    Returns:
        限流器实例
    """
    global _limiter
    if _limiter is None:
        _limiter = BinanceRateLimiter(requests_per_minute, safety_margin)
    return _limiter
