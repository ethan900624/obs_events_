"""
模組名稱: video_library.py
功能描述: 負責讀取 video_event.json 設定檔，管理影片清單。
          提供依標籤隨機選取影片片段 (含洗牌演算法) 的核心邏輯。
"""

import json
import os
import random
from typing import Dict, Any
from .models import VideoSegment
from .utils import TimeConverter

class VideoLibrary:
    """
    管理影片設定，並提供依標籤隨機選取影片片段的功能。
    這個類別封裝了所有讀取和解析 JSON 的邏輯。
    """
    def __init__(self, settings_path: str = "video_event.json"):
        self._settings = self._load_settings(settings_path)
        
        try:
            # 直接從優化後的結構讀取設定
            global_settings = self._settings["global_settings"]
            path_config = global_settings["path_config"]
            
            # 解析基礎路徑
            base_dir = os.path.dirname(os.path.abspath(settings_path))
            self._root_dir = os.path.normpath(os.path.join(base_dir, path_config.get("root", "./※素材")))
            self._raw_dir = os.path.normpath(os.path.join(self._root_dir, path_config["raw_videos"]))
            self._clips_dir = os.path.normpath(os.path.join(self._root_dir, path_config["clips"]))
            
            self._videos = self._settings["videos"]
        except (KeyError, IndexError) as e:
            print(f"❌ 錯誤：設定檔 {settings_path} 的結構不正確。缺少鍵或列表為空: {e}")
            raise ValueError(f"設定檔結構錯誤: {e}") from e
        
        # 初始化快取：預先建立標籤索引，確保查詢速度不受片段數量影響
        self._build_tag_cache()
        self._shuffle_pools = {} # 新增：洗牌池，用於確保隨機播放不重複

    @property
    def settings(self) -> Dict[str, Any]:
        """回傳完整的設定檔內容。"""
        return self._settings

    def _load_settings(self, json_path: str) -> Dict[str, Any]:
        """私有方法，負責讀取 JSON 設定檔。"""
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"❌ 錯誤：找不到設定檔 {json_path}")
            raise
        except json.JSONDecodeError:
            print(f"❌ 錯誤：設定檔 {json_path} 格式不正確。")
            raise

    def _build_tag_cache(self):
        """預先將所有片段依標籤分類，建立快取池 (Cache Pool)。"""
        self._tag_cache = {}
        total_segments = 0
        for video in self._videos:
            tags = video.get("tags", {})
            for tag_name, segments in tags.items():
                if tag_name not in self._tag_cache:
                    self._tag_cache[tag_name] = []
                
                for seg in segments:
                    self._tag_cache[tag_name].append((video, seg))
                    total_segments += 1
        print(f"📊 快取建立完成: 共 {len(self._videos)} 部影片, {total_segments} 個片段。可用標籤: {list(self._tag_cache.keys())}")

    def get_random_segment_by_tag(self, tag_type: str) -> VideoSegment:
        """
        公開方法，從設定檔中依標籤隨機抽取一個影片片段。
        這是外部與這個類別互動的主要介面。
        """
        # 1. 檢查標籤是否存在
        if tag_type not in self._tag_cache or not self._tag_cache[tag_type]:
            raise ValueError(f"❌ 找不到任何包含標籤 '{tag_type}' 的影片")

        # 2. 使用洗牌池邏輯 (Shuffle Bag) 確保不重複播放
        if tag_type not in self._shuffle_pools or not self._shuffle_pools[tag_type]:
            print(f"🔀 重置標籤 '{tag_type}' 的隨機池 (共 {len(self._tag_cache[tag_type])} 個片段)")
            pool = list(self._tag_cache[tag_type]) # 複製一份
            random.shuffle(pool)
            self._shuffle_pools[tag_type] = pool
        
        # 3. 從池子取出一個 (不放回)
        chosen_video, chosen_segment = self._shuffle_pools[tag_type].pop()
        print(f"🎲 從池中選取: {chosen_video['file_name']} (剩餘 {len(self._shuffle_pools[tag_type])} 個)")
        
        start_str = chosen_segment["start_time"]
        end_str = chosen_segment["end_time"]
        start_time = TimeConverter.to_seconds(start_str)
        end_time = TimeConverter.to_seconds(end_str)
        vol_mul = chosen_video.get("volume_multiplier", 1.0)
        full_path = os.path.abspath(os.path.join(self._raw_dir, chosen_video["file_name"]))

        # 優先尋找並使用已剪輯的片段
        if start_str != "full":
            file_root, _ = os.path.splitext(os.path.basename(chosen_video["file_name"]))
            safe_start = start_str.replace(":", "-")
            safe_end = end_str.replace(":", "-") if end_str not in ["full", "end"] else "end"
            clipped_filename = f"{file_root}_{safe_start}_{safe_end}.mkv"
            clipped_path = os.path.abspath(os.path.join(self._clips_dir, clipped_filename))
            if os.path.exists(clipped_path):
                print(f"✨ 發現已剪輯片段，使用優化檔案: {clipped_filename}")
                return VideoSegment(file_path=clipped_path, start_time="full", end_time="full", volume_multiplier=vol_mul)

        return VideoSegment(file_path=full_path, start_time=start_time, end_time=end_time, volume_multiplier=vol_mul)