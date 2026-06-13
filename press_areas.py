#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""简单的区域按压函数"""

import time
import win32gui
import win32con
from game_control import GameControl
import config

_game_control = None

def _init_game_control():
    global _game_control
    if _game_control is None:
        try:
            _game_control = GameControl()
            print("✅ 游戏控制初始化成功")
        except Exception as e:
            print(f"❌ 游戏控制初始化失败: {e}")
            raise
    return _game_control

def f(area_number: int):
    if area_number not in config.AREA_PRESS_TIMES:
        raise ValueError(f"无效的区域编号: {area_number}，有效范围: 1-7")
    try:
        game_control = _init_game_control()
        duration_ms = config.AREA_PRESS_TIMES[area_number]
        print(f"按压区域 {area_number} ({duration_ms}ms)")
        game_control.press_button(area_number)
        print(f"✅ 区域 {area_number} 按压完成")
    except Exception as e:
        print(f"❌ 区域 {area_number} 按压失败: {e}")
        raise

def press_area(area_number: int):
    return f(area_number)

def get_area_info():
    print("区域按压时间配置:")
    for area, duration in config.AREA_PRESS_TIMES.items():
        print(f"  区域{area}: {duration}ms")

def test_all_areas():
    print("测试所有区域...")
    for area in range(1, 8):
        try:
            f(area)
            time.sleep(0.5)
        except Exception as e:
            print(f"区域 {area} 测试失败: {e}")

def discard_tile(tile_num: int, action_type: int):
    if tile_num < 1 or tile_num > 15:
        raise ValueError(f"无效的牌编号: {tile_num}，有效范围: 1-15")
    if action_type not in [1, 2]:
        raise ValueError(f"无效的动作类型: {action_type}，有效值: 1(选中) 或 2(打出)")
    try:
        game_control = _init_game_control()
        hwnd = win32gui.FindWindow(None, config.GAME_WINDOW_TITLE)
        if not hwnd:
            raise Exception("找不到游戏窗口")
        rect = win32gui.GetWindowRect(hwnd)
        window_x, window_y = rect[0], rect[1]
        window_width = rect[2] - rect[0]
        window_height = rect[3] - rect[1]
        click_x, click_y = _calculate_tile_position(tile_num, window_x, window_y, window_width, window_height)
        if action_type == 1:
            print(f"🎯 选中第{tile_num}张牌: ({click_x}, {click_y})")
            game_control._click_at_position(click_x, click_y, config.DISCARD_TILE_CONFIG['single_click_duration'])
            print(f"✅ 选中完成")
        elif action_type == 2:
            print(f"🎯 打出第{tile_num}张牌: ({click_x}, {click_y})")
            game_control._click_at_position(click_x, click_y, config.DISCARD_TILE_CONFIG['double_click_duration'])
            time.sleep(config.DISCARD_TILE_CONFIG['click_interval'] / 1000.0)
            game_control._click_at_position(click_x, click_y, config.DISCARD_TILE_CONFIG['double_click_duration'])
            print(f"✅ 打出完成")
    except Exception as e:
        print(f"❌ 打出手牌失败: {e}")
        raise

def _calculate_tile_position(tile_num: int, window_x: int, window_y: int, window_width: int, window_height: int) -> tuple:
    if tile_num <= 13:
        hand_tiles = config.TILE_REGIONS['hand_tiles']
        relative_x = hand_tiles['relative_left'] + (tile_num - 1) * (hand_tiles['relative_width'] / config.DISCARD_TILE_CONFIG['hand_tiles_count'])
        relative_y = hand_tiles['relative_top'] + (hand_tiles['relative_height'] / 2)
        x = window_x + int(window_width * relative_x / 100)
        y = window_y + int(window_height * relative_y / 100)
    elif tile_num == 14:
        drawn_tile = config.TILE_REGIONS['drawn_tile']
        relative_x = drawn_tile['relative_left'] + (drawn_tile['relative_width'] / 2)
        relative_y = drawn_tile['relative_top'] + (drawn_tile['relative_height'] / 2)
        x = window_x + int(window_width * relative_x / 100)
        y = window_y + int(window_height * relative_y / 100)
    elif tile_num == 15:
        riichi_pos = config.TILE_REGIONS['riichi_position']
        relative_x = riichi_pos['relative_x']
        relative_y = riichi_pos['relative_y']
        x = window_x + int(window_width * relative_x / 100)
        y = window_y + int(window_height * relative_y / 100)
    return (x, y)

press = f
area = f
discard = discard_tile

if __name__ == "__main__":
    print("区域按压函数示例:")
    print("f(1) - 按压区域1")
    print("f(2) - 按压区域2")
    print("f(4) - 按压区域4（中间区域）")
    print("press(1) - 等同于 f(1)")
    print("area(1) - 等同于 f(1)")
    print()
    get_area_info()