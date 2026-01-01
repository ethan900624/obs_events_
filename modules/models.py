"""
模組名稱: models.py
功能描述: 定義專案中使用的資料結構 (Data Classes)。
          主要包含 VideoSegment 類別，用於標準化影片片段的資訊傳遞。
"""
import os
from typing import Union

class VideoSegment:
    """一個資料類別，用來存放影片片段的資訊。"""
    def __init__(self, file_path: str, start_time: Union[float, str], end_time: Union[float, str], volume_multiplier: float = 1.0):
        self.file_path = file_path
        self.start_time = start_time
        self.end_time = end_time
        self.volume_multiplier = volume_multiplier

    def __repr__(self) -> str:
        """提供一個清晰的物件字串表示法，方便除錯。"""
        return (f"VideoSegment(path='{os.path.basename(self.file_path)}', "
                f"start={self.start_time}, end={self.end_time}, "
                f"vol={self.volume_multiplier})")