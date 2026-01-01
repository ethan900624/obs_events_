"""
伺服器模式
python main.py server
"""

import time
import threading
import sys
from flask import Flask, jsonify
from modules.video_library import VideoLibrary
from modules.obs_controller import OBSController
from obswebsocket import exceptions, events

# ----------------------------------------------------------
# 主程式示範
# ----------------------------------------------------------

# --- 全域變數，供 Flask 路由使用 ---
app = Flask(__name__)
library: VideoLibrary = None
obs_controller: OBSController = None

@app.route('/play/<string:tag_type>', methods=['POST', 'GET'])
def play_video(tag_type: str):
    """
    Flask 路由，接收來自 Streamer.bot 的指令。
    例如，Streamer.bot 呼叫 http://127.0.0.1:5678/play/opening
    """
    # 將請求委託給 OBSController 處理
    result = obs_controller.handle_play_request(tag_type)
    
    # 根據回傳的 code 設定 HTTP 狀態碼
    status_code = result.get("code", 200)
    return jsonify(result), status_code

def run_playback_test(tag: str):
    """一個獨立的測試函式，用於快速驗證播放流程。"""
    print("\n--- Running in Test Mode ---")
    local_library = None
    local_obs_controller = None
    try:
        # 1. 初始化
        local_library = VideoLibrary("video_event.json")
        settings = local_library._settings
        obs_settings = settings["global_settings"]["obs"]

        # 2. 連線 OBS
        local_obs_controller = OBSController(
            host=obs_settings["webSocket"]["ip"],
            port=obs_settings["webSocket"]["port"],
            password=obs_settings["webSocket"]["password"]
        )
        local_obs_controller.connect()
        
        # 注入 library，讓 controller 能讀取設定與影片
        local_obs_controller.set_library(local_library)

        # 特殊處理待機循環測試
        if tag == "待機":
            print("🔄 啟動待機循環模式...")
            
            # 記錄開始前的場景，以便結束時返回原處 (實現 A/A - B/B)
            original_scene = local_obs_controller.get_current_program_scene()
            
            # 直接呼叫 handle_play_request 啟動循環
            local_obs_controller.handle_play_request("待機")
            
            input("⏸️  正在播放待機循環。按 Enter 鍵停止並返回原場景...")
            
            local_obs_controller.is_standby_mode = False
            print(f"🛑 停止循環，切換回原場景 '{original_scene}'。")
            local_obs_controller.set_current_scene(original_scene)
            
            # 根據 A/B 邏輯還原預覽場景 (若回到 A，預覽設為 B；若回到 B，預覽設為 A)
            transition_data = local_obs_controller.calculate_ab_transition(original_scene)
            time.sleep(0.2)
            local_obs_controller.set_current_preview_scene(transition_data["target_scene"])
            return

        # 3. 一般播放測試
        local_obs_controller.handle_play_request(tag)

        print("\n✅ 測試指令已發送。請檢查 OBS。")
        input("按 Enter 鍵結束測試...")

    except Exception as e:
        print(f"❌ 測試過程中發生錯誤: {e}")
    finally:
        if local_obs_controller and local_obs_controller._is_connected:
            local_obs_controller.disconnect()

def main():
    global library, obs_controller

    try:
        # 1. 初始化 VideoLibrary
        print("Initializing Video Library...")
        library = VideoLibrary("video_event.json")
        settings = library._settings # 取得解析後的設定
        
        # 2. 初始化並連線 OBS 控制器
        print("Connecting to OBS...")
        obs_settings = settings["global_settings"]["obs"]
        obs_controller = OBSController(
            host=obs_settings["webSocket"]["ip"],
            port=obs_settings["webSocket"]["port"],
            password=obs_settings["webSocket"]["password"]
        )
        obs_controller.connect()
        
        # 注入 library 並載入設定
        obs_controller.set_library(library)

        # 3. 註冊 OBS 事件處理器
        print("Registering OBS event handlers...")
        def on_scene_changed(message):
            print(f"📢 OBS 事件: 場景已切換到 '{message.getSceneName()}'")
        def on_media_input_playback_state_changed(message):
            state = message.getMediaState()
            print(f"📢 OBS 事件: 媒體來源 '{message.getInputName()}' 播放狀態變更為 '{state}'")
            # 只有在非計時播放（即播放完整影片）的情況下，才由這個事件觸發切換
            if message.getInputName() == obs_controller.SOURCE_MEDIA and state == "OBS_MEDIA_STATE_ENDED":
                
                # --- 防止衝突邏輯：檢查是否需要忽略此事件 ---
                if obs_controller.ignore_end_event_counter > 0:
                    print(f"🛡️ 忽略舊影片的結束事件 (剩餘忽略次數: {obs_controller.ignore_end_event_counter - 1})")
                    obs_controller.ignore_end_event_counter -= 1
                    return
                # ------------------------------------

                if not obs_controller.is_timed_playback:
                    
                    # 檢查是否處於待機循環模式
                    if obs_controller.is_standby_mode:
                        print(f"🔄 待機循環 (事件觸發)：播放下一部影片...")
                        if obs_controller.standby_callback:
                            # 使用執行緒避免阻塞事件處理
                            threading.Thread(target=obs_controller.standby_callback).start()
                        return

                    target = obs_controller.current_target_scene or obs_controller.SCENE_PREVIEW
                    preview_target = obs_controller.current_preview_target
                    
                    print(f"✅ 影片自然播放結束，自動切換回場景 '{target}'")
                    obs_controller.set_current_scene(target)
                    if preview_target:
                        # 使用執行緒來執行延遲設定，避免阻塞事件處理迴圈，並等待轉場完成
                        def set_preview_delayed():
                            time.sleep(0.5)
                            print(f"   同時設定預覽場景為 '{preview_target}'")
                            obs_controller.set_current_preview_scene(preview_target)
                        threading.Thread(target=set_preview_delayed).start()
        obs_controller.register_event_handler(events.CurrentProgramSceneChanged, on_scene_changed)
        obs_controller.register_event_handler(events.MediaInputPlaybackStateChanged, on_media_input_playback_state_changed)

        # 4. 啟動 Flask 伺服器來接收指令
        print("\n--- Python OBS Controller is running ---")
        print("Listening for commands at http://127.0.0.1:5678")
        print("Press CTRL+C to exit.")
        app.run(host='127.0.0.1', port=5678)

    except (ValueError, FileNotFoundError, exceptions.ConnectionFailure) as e:
        print(f"❌ 啟動失敗: {e}")
        sys.exit(1)
    finally:
        # 無論成功或失敗，都確保斷開連線
        if obs_controller and obs_controller._is_connected:
            obs_controller.disconnect()

if __name__ == "__main__":
    # 根據命令列參數決定執行模式
    # 執行 `py main.py server` -> 啟動伺服器
    # 執行 `py main.py` (無參數) -> 進入互動測試模式
    if len(sys.argv) > 1 and sys.argv[1].lower() == 'server':
        main()
    else:
        # 互動式測試模式
        while True:
            try:
                print("\n--- 互動測試模式 ---")
                temp_lib = VideoLibrary("video_event.json")
                
                all_tags = []
                seen_tags = set()
                for v in temp_lib._videos:
                    for tag in v.get("tags", {}):
                        if tag not in seen_tags:
                            seen_tags.add(tag)
                            all_tags.append(tag)
                
                if not all_tags:
                    print("❌ 在 video_event.json 中找不到任何可用的標籤。")
                    input("按 Enter 鍵離開...")
                    break

                print("可用的標籤:")
                for i, tag_name in enumerate(all_tags):
                    print(f"  {i+1}: {tag_name}")
                print("  q: 離開")

                choice = input(f"\n請輸入要測試的編號或標籤名稱 (或 'q' 離開): ").lower()

                if choice in ['q', 'quit']:
                    print("👋 離開測試模式。")
                    break

                selected_tag = None
                if choice.isdigit() and 0 < int(choice) <= len(all_tags):
                    selected_tag = all_tags[int(choice) - 1]
                elif choice in all_tags:
                    selected_tag = choice
                
                if selected_tag:
                    run_playback_test(selected_tag)
                else:
                    print(f"❌ 無效的輸入 '{choice}'。請重新輸入。")

            except (ValueError, IndexError, FileNotFoundError) as e:
                print(f"❌ 測試失敗: {e}")
                input("按 Enter 鍵離開...")
                break
