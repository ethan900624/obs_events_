"""
模組名稱: utils.py
功能描述: 提供專案通用的工具類別與函式。
          包含時間格式轉換 (TimeConverter) 等輔助功能。
"""

from datetime import datetime
from typing import Union

class TimeConverter:
    """一個專門處理時間格式轉換的工具類別"""
    @staticmethod
    def to_seconds(time_str: str) -> Union[float, str]:
        """將 'HH:MM:SS.ms' 格式的字串轉為秒數 (float)。"""
        if time_str in ["full", "end"]:
            return time_str
        try:
            dt = datetime.strptime(time_str, "%H:%M:%S.%f")
            return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1_000_000
        except ValueError:
            # 容錯處理：如果沒有微秒
            dt = datetime.strptime(time_str, "%H:%M:%S")
            return dt.hour * 3600 + dt.minute * 60 + dt.second
