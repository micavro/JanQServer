#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏自动化启动脚本"""

import os
import sys
import time
from main_game_automation import GameAutomation
import config

def check_requirements():
    print("=" * 50)
    print("检查运行要求")
    print("=" * 50)
    log_file = config.GAME_LOG_FILE_PATH
    if os.path.exists(log_file):
        print(f"✅ Log文件存在: {log_file}")
    else:
        print(f"⚠️ Log文件不存在: {log_file}")
        print("请确保游戏正在运行并生成Log文件")
    required_files = ['game_control.py', 'config.py', 'press_areas.py', 'actuator.exe']
    for file in required_files:
        if os.path.exists(file):
            print(f"✅ {file} 存在")
        else:
            print(f"❌ {file} 不存在")
            return False
    return True

def setup_log_file():
    print("=" * 50)
    print("Log文件设置")
    print("=" * 50)
    log_file = config.GAME_LOG_FILE_PATH
    if not os.path.exists(log_file):
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write("# JanQ游戏Log文件\n")
            print(f"✅ 创建Log文件: {log_file}")
        except Exception as e:
            print(f"❌ 创建Log文件失败: {e}")
            return False
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            print(f"📄 Log文件行数: {len(lines)}")
            if lines:
                print(f"📄 最后一行: {lines[-1].strip()}")
    except Exception as e:
        print(f"❌ 读取Log文件失败: {e}")
        return False
    return True

def start_automation():
    print("=" * 50)
    print("启动游戏自动化")
    print("=" * 50)
    if not check_requirements():
        print("❌ 运行要求检查失败")
        return False
    if not setup_log_file():
        print("❌ Log文件设置失败")
        return False
    try:
        automation = GameAutomation(config.GAME_LOG_FILE_PATH)
        print("✅ 自动化实例创建成功")
    except Exception as e:
        print(f"❌ 创建自动化实例失败: {e}")
        return False
    print("\n🚀 准备启动自动化程序...")
    print("请确保:")
    print("1. 游戏正在运行")
    print("2. Log文件正在生成")
    print("3. 游戏窗口可见")
    print("4. 以管理员权限运行此程序")
    choice = input("\n是否继续启动？(y/n): ").strip().lower()
    if choice != 'y':
        print("启动取消")
        return False
    try:
        automation.run()
        return True
    except Exception as e:
        print(f"❌ 自动化运行失败: {e}")
        return False

def main():
    print("JanQ游戏自动化启动器")
    print("=" * 50)
    print(f"Log文件路径: {config.GAME_LOG_FILE_PATH}")
    print(f"监控间隔: {config.LOG_MONITOR_INTERVAL}秒")
    print(f"BET按钮区域: {config.BET_BUTTON_AREA}")
    print(f"射击策略: {config.SHOOT_STRATEGY}")
    print()
    success = start_automation()
    if success:
        print("✅ 自动化程序正常结束")
    else:
        print("❌ 自动化程序异常结束")
    input("\n按回车键退出...")

if __name__ == "__main__":
    main()