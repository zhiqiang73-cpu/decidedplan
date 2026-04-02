"""
自定义异常类
用于币安数据下载器的错误处理
"""


class BinanceDownloaderError(Exception):
    """币安下载器基础异常"""
    pass


class BinanceAPIError(BinanceDownloaderError):
    """币安API错误"""

    def __init__(self, message: str, status_code: int = None, response: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class RateLimitError(BinanceAPIError):
    """API限流错误（HTTP 429）"""
    pass


class IPBannedError(BinanceAPIError):
    """IP被封禁错误（HTTP 418）"""
    pass


class ValidationError(BinanceDownloaderError):
    """数据验证错误"""
    pass


class CheckpointError(BinanceDownloaderError):
    """检查点错误"""
    pass


class DataProcessingError(BinanceDownloaderError):
    """数据处理错误"""
    pass


class ConfigurationError(BinanceDownloaderError):
    """配置错误"""
    pass
