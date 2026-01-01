"""
模組名稱: clip_video_segments.py
功能描述: 自動化工具腳本。
          讀取 video_event.json 中的片段資訊，使用 ffmpeg 批次剪輯出獨立的 .mkv 檔案。
          支援 Fast Seek 與 CRF 高品質編碼。
"""

import json
import os
import subprocess
import sys
from datetime import datetime

# 設定檔路徑 (指向專案根目錄的 video_event.json)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(BASE_DIR, "video_event.json")

class TimeParser:
    """處理時間格式轉換的工具"""
    @staticmethod
    def to_seconds(time_str):
        """將 'HH:MM:SS.ms' 格式的字串轉為秒數 (float)。"""
        if time_str in ["full", "end"]:
            return 0.0
        try:
            dt = datetime.strptime(time_str, "%H:%M:%S.%f")
            return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1_000_000
        except ValueError:
            try:
                dt = datetime.strptime(time_str, "%H:%M:%S")
                return dt.hour * 3600 + dt.minute * 60 + dt.second
            except ValueError:
                print(f"⚠️ 無法解析時間格式: {time_str}")
                return 0.0

def load_settings(path):
    if not os.path.exists(path):
        print(f"❌ 找不到設定檔: {path}")
        sys.exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def process_videos():
    print("🚀 開始批次剪輯與音量統一...")
    
    settings = load_settings(JSON_PATH)
    path_config = settings["global_settings"]["path_config"]
    
    root_path = path_config.get("root", "./※素材")
    abs_root = os.path.normpath(os.path.join(BASE_DIR, root_path))
    
    source_dir = os.path.join(abs_root, path_config["raw_videos"])
    output_dir = os.path.join(abs_root, path_config["clips"])
    
    print(f"📂 原始影片目錄: {source_dir}")
    print(f"📂 輸出片段目錄: {output_dir}")

    if not os.path.exists(source_dir):
        print(f"❌ 原始影片目錄不存在: {source_dir}")
        sys.exit(1)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"📁 已建立輸出目錄: {output_dir}")

    videos = settings.get("videos", [])
    processed_segments = set()
    expected_files = set()

    for video_info in videos:
        tags = video_info.get("tags", {})
        if not tags: continue

        file_name = video_info["file_name"]
        input_path = os.path.join(source_dir, file_name)
        
        if not os.path.exists(input_path):
            print(f"⚠️ 跳過找不到的檔案: {file_name}")
            continue

        print(f"\n🎥 正在處理來源檔案: {file_name}")
        file_root, file_ext = os.path.splitext(file_name)

        for tag_name, segments in tags.items():
            for i, segment in enumerate(segments):
                start_str = segment["start_time"]
                end_str = segment["end_time"]
                
                if start_str == "full":
                    print(f"   ⏭️  跳過完整影片設定 [{tag_name}] (full)")
                    continue

                segment_key = (file_name, start_str, end_str)
                if segment_key in processed_segments: continue
                processed_segments.add(segment_key)

                safe_start = start_str.replace(":", "-") if start_str != "full" else "start"
                safe_end = end_str.replace(":", "-") if end_str not in ["full", "end"] else "end"
                output_filename = f"{file_root}_{safe_start}_{safe_end}.mkv"
                expected_files.add(output_filename)
                output_path = os.path.join(output_dir, output_filename)

                if os.path.exists(output_path):
                    print(f"   ⏭️  檔案已存在，跳過: {output_filename}")
                    continue
                
                print(f"   ✂️  剪輯片段 [{tag_name}]: {start_str} -> {end_str}")
                cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats']
                start_seconds = 0.0
                if start_str != "full":
                    start_seconds = TimeParser.to_seconds(start_str)
                
                cmd.extend(['-i', input_path])
                
                # Slow Seek: -ss after -i (精確剪輯，解決畫面定格與起點不準問題)
                if start_str != "full":
                    cmd.extend(['-ss', start_str])

                if end_str not in ["full", "end"]:
                    end_seconds = TimeParser.to_seconds(end_str)
                    duration = end_seconds - start_seconds
                    if duration > 0: cmd.extend(['-t', str(duration)])
                    else: continue

                cmd.extend([
                    '-map', '0',
                    '-c:v', 'libx264', '-crf', '18', '-preset', 'slow',
                    '-c:a', 'copy',
                    output_path
                ])
                
                try:
                    subprocess.run(cmd, check=True)
                    print("      ✅ 完成")
                except subprocess.CalledProcessError as e:
                    print(f"      ❌ ffmpeg 執行失敗: {e}")
                except FileNotFoundError:
                    print("      ❌ 錯誤: 找不到 ffmpeg。")
                    return

    # 8. 清理孤兒檔案 (不在 JSON 設定中的 .mkv 檔案)
    print("\n🧹 開始清理舊片段...")
    if os.path.exists(output_dir):
        for filename in os.listdir(output_dir):
            if filename.endswith(".mkv") and filename not in expected_files:
                file_path = os.path.join(output_dir, filename)
                try:
                    os.remove(file_path)
                    print(f"   🗑️  刪除孤兒檔案: {filename}")
                except OSError as e:
                    print(f"   ❌ 無法刪除檔案 {filename}: {e}")

    print("\n🎉 所有作業已完成！")

if __name__ == "__main__":
    process_videos()