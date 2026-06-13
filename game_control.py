import time
import win32gui
import win32con
import subprocess
import os
import ctypes
from typing import Tuple, Optional
import config

class GameControl:
    def __init__(self):
        self.game_window = None
        self.window_rect = None
        self.button_position = None
        self.check_permissions()
        self.find_game_window()
        self.calculate_button_position()
    
    def check_permissions(self):
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
            if is_admin:
                print("✅ 程序以管理员权限运行")
            else:
                print("⚠️ 程序未以管理员权限运行，某些窗口操作可能失败")
                print("建议：右键点击程序 -> '以管理员身份运行'")
        except Exception as e:
            print(f"⚠️ 无法检查权限状态: {e}")
        
    def find_game_window(self):
        def enum_windows_callback(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if config.GAME_WINDOW_TITLE in window_title:
                    windows.append(hwnd)
            return True
            
        windows = []
        win32gui.EnumWindows(enum_windows_callback, windows)
        
        if windows:
            self.game_window = windows[0]
            window_title = win32gui.GetWindowText(self.game_window)
            print(f"找到游戏窗口: {window_title}")
            self.window_rect = win32gui.GetWindowRect(self.game_window)
            x, y, right, bottom = self.window_rect
            width = right - x
            height = bottom - y
            print(f"窗口位置: ({x}, {y})")
            print(f"窗口大小: {width} x {height}")
            self.activate_game_window()
        else:
            print(f"❌ 未找到游戏窗口: {config.GAME_WINDOW_TITLE}")
            print("请确保游戏正在运行")
    
    def activate_game_window(self):
        try:
            if self.game_window:
                win32gui.SetForegroundWindow(self.game_window)
                win32gui.ShowWindow(self.game_window, win32con.SW_RESTORE)
                print("✅ 游戏窗口已激活")
        except Exception as e:
            print(f"❌ 激活游戏窗口失败: {e}")
    
    def calculate_button_position(self):
        if not self.window_rect:
            print("❌ 无法计算按钮位置：游戏窗口未找到")
            return
        
        x, y, right, bottom = self.window_rect
        width = right - x
        height = bottom - y
        
        button_x = right + int(width * config.BUTTON_RELATIVE_OFFSET['right_offset'])
        button_y = bottom + int(height * config.BUTTON_RELATIVE_OFFSET['bottom_offset'])
        
        self.button_position = (button_x, button_y)
        print(f"计算按钮位置: ({button_x}, {button_y})")
    
    def press_button_cpp(self, area: int, duration_ms: int = None) -> bool:
        if not self.button_position:
            print("❌ 按钮位置未计算")
            return False
        
        if duration_ms is None:
            duration_ms = config.AREA_PRESS_TIMES.get(area, config.DEFAULT_BET_TIME)
        
        x, y = self.button_position
        
        try:
            if config.USE_CPP_ACTUATOR and os.path.exists(config.ACTUATOR_PATH):
                cmd = [config.ACTUATOR_PATH, str(x), str(y), str(duration_ms), "left"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    print(f"✅ C++执行器成功: 区域{area} ({x},{y}) {duration_ms}ms")
                    return True
                else:
                    print(f"❌ C++执行器失败: {result.stderr}")
                    return False
            else:
                print(f"❌ C++执行器不可用: {config.ACTUATOR_PATH}")
                return False
        except subprocess.TimeoutExpired:
            print(f"❌ C++执行器超时")
            return False
        except Exception as e:
            print(f"❌ C++执行器异常: {e}")
            return False
    
    def press_button_python(self, area: int, duration_ms: int = None) -> bool:
        if not self.button_position:
            print("❌ 按钮位置未计算")
            return False
        
        if duration_ms is None:
            duration_ms = config.AREA_PRESS_TIMES.get(area, config.DEFAULT_BET_TIME)
        
        x, y = self.button_position
        
        try:
            import win32api
            import win32con
            
            start_time = time.time()
            win32api.SetCursorPos((x, y))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
            
            while (time.time() - start_time) * 1000 < duration_ms:
                time.sleep(0.001)
            
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            print(f"✅ Python执行器成功: 区域{area} ({x},{y}) {duration_ms}ms")
            return True
        except Exception as e:
            print(f"❌ Python执行器失败: {e}")
            return False
    
    def press_button(self, area: int, duration_ms: int = None) -> bool:
        if config.USE_CPP_ACTUATOR:
            return self.press_button_cpp(area, duration_ms)
        else:
            return self.press_button_python(area, duration_ms)
    
    def get_window_info(self) -> dict:
        if not self.game_window:
            return {"error": "游戏窗口未找到"}
        
        try:
            window_title = win32gui.GetWindowText(self.game_window)
            window_rect = win32gui.GetWindowRect(self.game_window)
            is_visible = win32gui.IsWindowVisible(self.game_window)
            
            return {
                "title": window_title,
                "rect": window_rect,
                "visible": is_visible,
                "button_position": self.button_position
            }
        except Exception as e:
            return {"error": f"获取窗口信息失败: {e}"}
    
    def _click_at_position(self, x: int, y: int, duration_ms: int):
        """在指定位置执行点击操作"""
        try:
            if config.USE_CPP_ACTUATOR and os.path.exists(config.ACTUATOR_PATH):
                cmd = [config.ACTUATOR_PATH, str(x), str(y), str(duration_ms), "left"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return True
                else:
                    print(f"❌ C++执行器点击失败: {result.stderr}")
                    return False
            else:
                import win32api
                import win32con
                start_time = time.time()
                win32api.SetCursorPos((x, y))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                while (time.time() - start_time) * 1000 < duration_ms:
                    time.sleep(0.001)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
                return True
        except Exception as e:
            print(f"❌ 点击操作失败: {e}")
            return False
    
    def refresh_window_info(self):
        self.find_game_window()
        self.calculate_button_position()

# 全局游戏控制实例
game_control = GameControl()

def press_area(area: int, duration_ms: int = None) -> bool:
    return game_control.press_button(area, duration_ms)

def get_game_window_info() -> dict:
    return game_control.get_window_info()

def refresh_game_window() -> bool:
    try:
        game_control.refresh_window_info()
        return game_control.game_window is not None
    except Exception as e:
        print(f"❌ 刷新游戏窗口失败: {e}")
        return False