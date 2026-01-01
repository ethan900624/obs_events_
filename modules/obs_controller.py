"""
模組名稱: obs_controller.py
功能描述: 負責與 OBS WebSocket 進行連線與通訊。
          包含場景切換、媒體播放控制、計時器邏輯與事件監聽。
"""

import sys
import time
import threading
from typing import Dict, Any, Optional
from obswebsocket import obsws, requests, exceptions
from .models import VideoSegment

class OBSController:
    """負責連線並控制 OBS 的類別。"""
    def __init__(self, host: str, port: int, password: str):
        self._ws = obsws(host, port, password)
        self._is_connected = False
        self.is_timed_playback = False # 新增旗標，用於追蹤是否為計時播放
        self.active_timer_thread = None # 新增：追蹤當前的計時器執行緒
        self.current_target_scene = None # 新增：儲存本次播放結束後要切換的目標場景
        self.current_preview_target = None # 新增：儲存本次播放結束後要設定的預覽場景
        self.is_standby_mode = False # 新增：標記是否處於待機循環模式
        self.standby_callback = None # 新增：待機循環的回呼函式
        self.library = None # 新增：持有 VideoLibrary 的參照
        
        # 場景與來源設定 (將在 set_library 中載入)
        self.SCENE_MAIN = ""
        self.SCENE_EVENT = ""
        self.SCENE_PREVIEW = ""
        self.SOURCE_MEDIA = ""
        self.SOURCE_BG_MAIN = ""
        self.SOURCE_BG_PREVIEW = ""
        self.obs_settings = {}

    def connect(self):
        """連線到 OBS WebSocket。"""
        try:
            self._ws.connect()
            self._is_connected = True
            print("✅ 成功連線到 OBS WebSocket。")
        except exceptions.ConnectionFailure as e:
            print(f"❌ 無法連線到 OBS WebSocket: {e}")
            sys.exit(1)

    def disconnect(self):
        """中斷與 OBS WebSocket 的連線。"""
        if not self._is_connected:
            return
        self._ws.disconnect()
        self.active_timer_thread = None
        self._is_connected = False
        print("🔌 已中斷與 OBS WebSocket 的連線。")

    def set_library(self, library):
        """
        注入 VideoLibrary 實例，並載入相關場景設定。
        這是初始化控制器的關鍵步驟。
        """
        self.library = library
        self._parse_scene_settings()
        # 設定待機回呼指向自身的 play_standby_video 方法
        self.standby_callback = self.play_standby_video

    def _parse_scene_settings(self):
        """(私有方法) 從 library 設定中解析場景與來源名稱，儲存為實例變數。"""
        if not self.library: return
        obs_settings = self.library.settings["global_settings"]["obs"]
        scenes = obs_settings["scenes"]
        
        self.SCENE_MAIN = scenes["main_output"]["name"]
        self.SCENE_EVENT = scenes["obs_event"]["name"]
        self.SCENE_PREVIEW = scenes["transition_preview"]["name"]
        
        self.SOURCE_MEDIA = scenes["obs_event"]["sources"]["media_player"]
        self.SOURCE_BG_MAIN = scenes["obs_event"]["sources"]["main_output"]
        # 若設定檔中沒有 transition_preview 來源，則預設使用場景名稱
        self.SOURCE_BG_PREVIEW = scenes["obs_event"]["sources"].get("transition_preview", self.SCENE_PREVIEW)
        
        self.obs_settings = obs_settings
        print(f"✅ OBS 控制器已載入場景設定: 主畫面='{self.SCENE_MAIN}', 事件='{self.SCENE_EVENT}'")

    def calculate_ab_transition(self, current_scene: str) -> Dict[str, str]:
        """
        計算 A/B 場景切換的目標與背景來源。
        根據當前場景 (主畫面或轉場預覽)，決定下一個目標場景與背景。
        """
        if current_scene == self.SCENE_PREVIEW:
            # 當前在 [轉場預覽] -> 去 [主畫面]
            return {
                "target_scene": self.SCENE_MAIN,
                "preview_scene": self.SCENE_PREVIEW,
                "bg_source": self.SOURCE_BG_PREVIEW,
                "hide_source": self.SOURCE_BG_MAIN
            }
        else:
            # 當前在 [主畫面] (或其它) -> 去 [轉場預覽]
            return {
                "target_scene": self.SCENE_PREVIEW,
                "preview_scene": self.SCENE_MAIN,
                "bg_source": self.SOURCE_BG_MAIN,
                "hide_source": self.SOURCE_BG_PREVIEW
            }

    def play_standby_video(self):
        """
        播放待機影片 (循環邏輯)。
        此方法會被計時器或事件回呼重複呼叫，形成無限循環。
        """
        if not self.library: return
        try:
            segment = self.library.get_random_segment_by_tag("待機")
            print(f"🔄 播放待機影片: {segment}")
            # 待機模式下，目標場景設為 SCENE_MAIN (作為停止時的預設返回場景)
            # 預覽場景設為 SCENE_PREVIEW，確保 A/B 邏輯在待機結束後能正確銜接
            self.play_video_segment(
                self.SCENE_EVENT,
                self.SOURCE_MEDIA,
                self.SOURCE_BG_MAIN,
                segment,
                target_scene_name=self.SCENE_MAIN,
                preview_target_scene_name=self.SCENE_PREVIEW
            )
        except Exception as e:
            print(f"❌ 播放待機影片失敗: {e}")

    def handle_play_request(self, tag_type: str) -> Dict[str, Any]:
        """
        處理來自外部 (如 API) 的播放請求。
        包含待機模式切換、影片選取、A/B 場景計算與播放指令下達。
        """
        print(f"\nReceived request to play tag: {tag_type}")
        
        # 1. 處理待機指令
        if tag_type == "待機":
            self.is_standby_mode = True
            self.play_standby_video()
            return {"status": "success", "message": "Started standby loop", "code": 200}

        # 2. 處理一般指令 (打斷待機)
        if self.is_standby_mode:
            print("🛑 收到新指令，停止待機循環。")
            self.is_standby_mode = False

        if not self.library:
             return {"status": "error", "message": "Library not initialized", "code": 500}

        try:
            # 3. 選取影片
            selected_segment = self.library.get_random_segment_by_tag(tag_type)
            print(f"✅ 已為標籤 '{tag_type}' 選擇影片: {selected_segment}")

            # 4. 計算 A/B 場景
            current_scene = self.get_current_program_scene()
            transition_data = self.calculate_ab_transition(current_scene)

            # 5. 執行播放
            self.play_video_segment(
                self.SCENE_EVENT,
                self.SOURCE_MEDIA,
                transition_data["bg_source"],
                selected_segment,
                target_scene_name=transition_data["target_scene"],
                preview_target_scene_name=transition_data["preview_scene"],
                source_to_hide=transition_data["hide_source"]
            )
            return {
                "status": "success", 
                "message": f"Playing segment for tag '{tag_type}'", 
                "segment": repr(selected_segment),
                "code": 200
            }
        except ValueError as e:
            print(f"❌ 錯誤: {e}")
            return {"status": "error", "message": str(e), "code": 404}
        except Exception as e:
            print(f"❌ 伺服器內部錯誤: {e}")
            return {"status": "error", "message": f"An internal error occurred: {e}", "code": 500}

    def register_event_handler(self, event_type, handler_func):
        """註冊 OBS WebSocket 事件處理器。"""
        self._ws.register(handler_func, event_type)
        print(f"👂 已註冊 '{event_type}' 事件處理器。")

    def get_current_program_scene(self):
        """取得當前的主場景名稱"""
        try:
            return self._ws.call(requests.GetCurrentProgramScene()).getCurrentProgramSceneName()
        except Exception as e:
            print(f"❌ 無法取得當前場景: {e}")
            return None

    def set_current_scene(self, scene_name: str):
        """更安全地設定當前節目場景，會先檢查場景是否存在。"""
        try:
            scene_list = self._ws.call(requests.GetSceneList())
            if any(s['sceneName'] == scene_name for s in scene_list.getScenes()):
                self._ws.call(requests.SetCurrentProgramScene(sceneName=scene_name))
            else:
                print(f"❌ 警告：嘗試切換到一個不存在的場景 '{scene_name}'。操作已取消。")
        except Exception as e:
            print(f"❌ 切換場景時發生錯誤: {e}")

    def set_current_preview_scene(self, scene_name: str):
        """設定當前預覽場景 (Studio Mode)。"""
        try:
            self._ws.call(requests.SetCurrentPreviewScene(sceneName=scene_name))
        except Exception as e:
            print(f"⚠️ 無法設定預覽場景 (可能未開啟 Studio Mode): {e}")

    def _wait_for_media_duration(self, source_name: str, max_retries: int = 20) -> int:
        """(私有方法) 嘗試獲取媒體長度，帶有重試機制。"""
        for i in range(max_retries):
            try:
                time.sleep(0.05)
                status = self._ws.call(requests.GetMediaInputStatus(inputName=source_name))
                duration = status.getMediaDuration()
                if duration is not None and duration > 0:
                    return duration
            except Exception:
                pass
        return -1

    def _timer_worker(self, delay: float, target_scene: str, preview_target: str):
        """(私有方法) 計時器執行緒的工作函式。"""
        time.sleep(0.1)
        if threading.current_thread() != self.active_timer_thread:
            return

        sleep_time = delay - 0.1
        if sleep_time > 0:
            time.sleep(sleep_time)
        
        if self.is_standby_mode:
            print(f"🔄 待機循環：播放下一部影片...")
            if self.standby_callback:
                self.standby_callback()
            return

        print(f"✅ 時間到，自動切換回場景 '{target_scene}'")
        self.set_current_scene(target_scene)
        
        if preview_target:
            time.sleep(0.5)
            print(f"   同時設定預覽場景為 '{preview_target}'")
            self.set_current_preview_scene(preview_target)
            
        self.active_timer_thread = None

    def play_video_segment(self, scene_name: str, source_name: str, background_source_name: str, segment: VideoSegment, target_scene_name: str, preview_target_scene_name: str = None, source_to_hide: str = None):
        """在指定的場景和來源中播放影片片段。"""
        print(f"🎬 執行播放指令：")
        print(f"   影片路徑: {segment.file_path}")
        
        self.is_timed_playback = False
        self.current_target_scene = target_scene_name
        self.current_preview_target = preview_target_scene_name

        if source_to_hide:
            try:
                item_id = self._ws.call(requests.GetSceneItemId(sceneName=scene_name, sourceName=source_to_hide)).getSceneItemId()
                self._ws.call(requests.SetSceneItemEnabled(sceneName=scene_name, sceneItemId=item_id, sceneItemEnabled=False))
            except Exception:
                pass

        try:
            print(f"   設定背景: 顯示 '{background_source_name}' (隱藏 '{source_to_hide}')...")
            item_id = self._ws.call(requests.GetSceneItemId(sceneName=scene_name, sourceName=background_source_name)).getSceneItemId()
            self._ws.call(requests.SetSceneItemEnabled(sceneName=scene_name, sceneItemId=item_id, sceneItemEnabled=True))
        except Exception as e:
            print(f"⚠️ 警告：無法啟用背景來源 '{background_source_name}'。錯誤: {e}")

        print(f"   預先靜音 '{source_name}'...")
        self._ws.call(requests.SetInputMute(inputName=source_name, inputMuted=True))

        print(f"   設定來源 '{source_name}' 的檔案路徑...")
        self._ws.call(requests.SetInputSettings(inputName=source_name, inputSettings={'local_file': segment.file_path}))

        print(f"   設定音量倍率: {segment.volume_multiplier}x")
        self._ws.call(requests.SetInputVolume(inputName=source_name, inputVolumeMul=segment.volume_multiplier))

        current_scene = self.get_current_program_scene()
        if current_scene != scene_name:
            print(f"   切換到場景 '{scene_name}'...")
            self._ws.call(requests.SetCurrentProgramScene(sceneName=scene_name))
            time.sleep(0.1)
        else:
            print(f"   已在場景 '{scene_name}'，跳過切換動作。")

        if isinstance(segment.start_time, (int, float)):
            start_milliseconds = int(segment.start_time * 1000)
            for i in range(5):
                print(f"   嘗試設定開始時間 ({i+1}/5): {segment.start_time} 秒")
                self._ws.call(requests.SetMediaInputCursor(inputName=source_name, mediaCursor=start_milliseconds))
                time.sleep(0.02)

        print(f"   恢復 '{source_name}' 音訊並播放...")
        self._ws.call(requests.SetInputMute(inputName=source_name, inputMuted=False))
        self._ws.call(requests.TriggerMediaInputAction(inputName=source_name, mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"))

        media_duration_ms = self._wait_for_media_duration(source_name)
        if media_duration_ms <= 0:
            print(f"⚠️ 警告：無法獲取影片 '{source_name}' 的長度 (嘗試 20 次失敗)，將退回完整播放模式。")

        commanded_start_sec = segment.start_time if isinstance(segment.start_time, (int, float)) else 0.0
        end_sec = segment.end_time if isinstance(segment.end_time, (int, float)) else (-1.0)
        if segment.end_time in ["end", "full"] and media_duration_ms > 0:
            end_sec = media_duration_ms / 1000.0

        if end_sec > 0:
            try:
                status = self._ws.call(requests.GetMediaInputStatus(inputName=source_name))
                actual_start_ms = status.getMediaCursor()
            except Exception:
                actual_start_ms = None
            actual_start_sec = actual_start_ms / 1000.0 if actual_start_ms is not None and actual_start_ms >= 0 else commanded_start_sec
            play_duration = (end_sec - actual_start_sec) + 0.2

            if play_duration > 0:
                print(f"   期望從 {commanded_start_sec:.2f}s 開始，實際從 {actual_start_sec:.2f}s 開始，播放 {play_duration:.2f} 秒後結束。")
                self.is_timed_playback = True
                self.active_timer_thread = threading.Thread(target=self._timer_worker, args=(play_duration, target_scene_name, preview_target_scene_name))
                self.active_timer_thread.start()
                return

        self.is_timed_playback = False
        print(f"   影片將完整播放，結束後由 OBS 事件觸發切換。")