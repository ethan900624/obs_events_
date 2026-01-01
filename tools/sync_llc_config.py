"""
模組名稱: sync_llc_config.py
功能描述: 自動化工具腳本。
          1. 讀取 LosslessCut 的專案檔 (.llc) 並同步到 video_event.json。
          2. 自動分析影片響度 (LUFS) 並計算音量倍率。
          3. 依檔名對影片清單進行自然排序。
"""
"""
強制重新計算模式:
python tools/sync_llc_config.py --reset
"""

import json
import os
import re
from datetime import timedelta
import locale
import subprocess
import sys

# 設定檔路徑 (指向專案根目錄的 video_event.json)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(BASE_DIR, "video_event.json")

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    # 先轉成字串，使用標準縮排
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    
    # 優化可讀性：將 start_time/end_time 壓縮成一行
    pattern1 = r'\{\s*"start_time":\s*"([^"]+)",\s*"end_time":\s*"([^"]+)"\s*\}'
    json_str = re.sub(pattern1, r'{ "start_time": "\1", "end_time": "\2" }', json_str, flags=re.DOTALL)
    
    pattern2 = r'\{\s*"end_time":\s*"([^"]+)",\s*"start_time":\s*"([^"]+)"\s*\}'
    json_str = re.sub(pattern2, r'{ "start_time": "\2", "end_time": "\1" }', json_str, flags=re.DOTALL)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(json_str)
    print(f"💾 已寫入設定檔: {path}")

def seconds_to_hms(seconds):
    """將秒數轉換為 HH:MM:SS.mmm 格式"""
    td = timedelta(seconds=float(seconds))
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    millis = int(td.microseconds / 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

def parse_llc_content(content):
    """解析 .llc 檔案內容 (支援 JSON 與 JS Object 格式)。"""
    try:
        data = json.loads(content)
        return data
    except json.JSONDecodeError:
        media_file_match = re.search(r"mediaFileName:\s*['\"](.+?)['\"]", content)
        media_file_name = media_file_match.group(1) if media_file_match else None

        segments = []
        segments_match = re.search(r"cutSegments:\s*\[(.*?)\]", content, re.DOTALL)
        if segments_match:
            inner_content = segments_match.group(1)
            segment_blocks = re.findall(r"\{([^{}]+)\}", inner_content)
            
            for block in segment_blocks:
                start_match = re.search(r"start:\s*([\d\.]+)", block)
                end_match = re.search(r"end:\s*([\d\.]+)", block)
                
                if start_match or end_match:
                    start_val = float(start_match.group(1)) if start_match else 0.0
                    end_val = float(end_match.group(1)) if end_match else None
                    segments.append({"start": start_val, "end": end_val})
        
        return {"mediaFileName": media_file_name, "cutSegments": segments}

def get_volume_multiplier(file_path, target_lufs=-14.0):
    """使用 ffmpeg 檢測影片響度，並計算達到目標 LUFS 所需的音量倍率。"""
    try:
        cmd = [
            'ffmpeg', '-hide_banner', '-nostats',
            '-i', file_path,
            '-vn', '-sn', '-dn',
            '-af', f'loudnorm=I={target_lufs}:TP=-1:print_format=json',
            '-f', 'null', '-'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        json_match = re.search(r'\{.*"input_i".*\}', result.stderr, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            input_i = float(data['input_i'])
            print(f"      📊 偵測響度: {input_i} LUFS")
            delta_db = target_lufs - input_i
            multiplier = 10 ** (delta_db / 20)
            return round(max(0.01, min(multiplier, 3.0)), 3)
    except Exception as e:
        print(f"⚠️ 音量分析失敗 {os.path.basename(file_path)}: {e}")
    return 1.0

def update_video_event():
    force_recalc = "--reset" in sys.argv
    if force_recalc:
        print("🔄 已啟用強制重新計算模式：將忽略舊有音量數據。")

    print("🚀 開始從 LLC 設定檔更新 video_event.json ...")
    
    if not os.path.exists(JSON_PATH):
        print(f"❌ 找不到設定檔: {JSON_PATH}")
        return

    current_data = load_json(JSON_PATH)
    path_config = current_data["global_settings"]["path_config"]
    root_path = path_config.get("root", "./※素材")
    abs_root = os.path.normpath(os.path.join(BASE_DIR, root_path))
    
    raw_video_dir = os.path.join(abs_root, path_config["raw_videos"])
    llc_dir = os.path.join(abs_root, "losslesscut剪輯設定檔")

    print(f"📂 原始影片目錄: {raw_video_dir}")
    print(f"📂 LLC 設定檔目錄: {llc_dir}")

    audio_norm_setting = current_data.get("global_settings", {}).get("audio_normalization", "-14.0")
    target_lufs = -14.0
    try:
        match = re.search(r"([-\d\.]+)", str(audio_norm_setting))
        if match: target_lufs = float(match.group(1))
    except Exception: pass
    print(f"🎚️ 目標響度: {target_lufs} LUFS")

    # 自動偵測設定變更：若目標響度改變，強制重新計算
    last_applied_norm = current_data.get("global_settings", {}).get("_applied_audio_normalization")
    if last_applied_norm is not None and str(last_applied_norm) != str(audio_norm_setting):
        print(f"🔄 偵測到音量設定變更 ({last_applied_norm} -> {audio_norm_setting})，自動啟用重新計算。")
        force_recalc = True

    if not os.path.exists(raw_video_dir):
        print(f"❌ 原始影片目錄不存在")
        return

    llc_data_map = {}
    if os.path.exists(llc_dir):
        for filename in os.listdir(llc_dir):
            if filename.endswith(".llc"):
                try:
                    with open(os.path.join(llc_dir, filename), 'r', encoding='utf-8') as f:
                        parsed = parse_llc_content(f.read())
                        if parsed and parsed.get("mediaFileName"):
                            llc_data_map[os.path.basename(parsed["mediaFileName"]).lower()] = parsed.get("cutSegments", [])
                except Exception as e:
                    print(f"⚠️ 解析 LLC 檔案失敗 {filename}: {e}")

    new_videos_list = []
    video_extensions = ('.mkv', '.mp4', '.mov', '.avi', '.webm')
    existing_videos = {v["file_name"]: v for v in current_data.get("videos", [])}

    for filename in os.listdir(raw_video_dir):
        if filename.lower().endswith(video_extensions):
            print(f"🎥 處理影片: {filename}")
            video_entry = {
                "file_name": filename,
                "tags": {"待機": [ { "start_time": "full", "end_time": "full" } ]}
            }
            
            if not force_recalc and filename in existing_videos and "volume_multiplier" in existing_videos[filename]:
                video_entry["volume_multiplier"] = existing_videos[filename]["volume_multiplier"]
            else:
                print(f"   🔊 正在分析原始影片音量...")
                video_entry["volume_multiplier"] = get_volume_multiplier(os.path.join(raw_video_dir, filename), target_lufs)
                print(f"      ↳ 建議音量倍率: {video_entry['volume_multiplier']}x")
            
            if filename.lower() in llc_data_map:
                segments = llc_data_map[filename.lower()]
                if segments:
                    clip_segments = []
                    for seg in segments:
                        start_str = seconds_to_hms(seg.get("start", 0))
                        end_str = seconds_to_hms(seg.get("end")) if seg.get("end") is not None else "end"
                        clip_segments.append({"start_time": start_str, "end_time": end_str})
                    if clip_segments:
                        video_entry["tags"]["影片片段"] = clip_segments
                        print(f"   ✅ 找到 {len(clip_segments)} 個剪輯片段")

            new_videos_list.append(video_entry)

    try:
        locale.setlocale(locale.LC_COLLATE, '')
        new_videos_list.sort(key=lambda x: locale.strxfrm(x['file_name']))
    except (locale.Error, ImportError):
        new_videos_list.sort(key=lambda x: x['file_name'])

    current_data["videos"] = new_videos_list
    current_data["global_settings"]["_applied_audio_normalization"] = audio_norm_setting
    save_json(JSON_PATH, current_data)
    print(f"🎉 更新完成！共處理 {len(new_videos_list)} 個影片並已排序。")

if __name__ == "__main__":
    update_video_event()