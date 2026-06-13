#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JanQ游戏自动化主程序"""

import json
import time
import os
import re
from typing import Dict, List, Optional, Tuple
from press_areas import f
import config
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'JanQcore'))
from JanQcore.game_main import JanQcore

class GameAutomation:
    def __init__(self, log_file_path: str = "game_log.txt"):
        self.log_file_path = log_file_path
        self.last_position = 0
        self.game_state = {}
        self.nyukyu_black_list = []
        self.challenge_bonus_tenpai_list = []
        self.stats_file_path = "game_statistics.json"
        self.statistics = self.load_statistics()
        print("✅ 游戏自动化系统初始化完成")
        print(f"监控Log文件: {log_file_path}")
        print(f"统计数据文件: {self.stats_file_path}")
    
    def read_log_file(self) -> List[str]:
        try:
            if not os.path.exists(self.log_file_path):
                return []
            with open(self.log_file_path, 'r', encoding='utf-8') as f:
                f.seek(self.last_position)
                new_lines = f.readlines()
                self.last_position = f.tell()
                return new_lines
        except Exception as e:
            print(f"❌ 读取Log文件失败: {e}")
            return []
    
    def parse_log_line(self, line: str) -> Optional[Dict]:
        try:
            match = re.search(r'\{.*\}', line)
            if match:
                json_str = match.group()
                return json.loads(json_str)
            if "SetNyukyuBlack called with list =" in line:
                match = re.search(r'list = \[(.*)\]', line)
                if match:
                    black_list_str = match.group(1)
                    self.nyukyu_black_list = [int(x.strip()) for x in black_list_str.split(',')]
                    print(f"📋 更新入球黑名单: {self.nyukyu_black_list}")
            return None
        except Exception as e:
            print(f"❌ 解析Log行失败: {e}")
            return None
    
    def get_discard_pos(self, m_pais: List[int], m_dra_pai_id: int, m_ura_dra_pai_id: int, m_balls: int) -> tuple:
        try:
            m_pais = [pai for pai in m_pais if pai != 9999]
            if len(m_pais) == 14:
                discard_pos, is_win, is_riichi = JanQcore(m_pais, m_balls)
                if is_win:
                    return 0, 1
                else:
                    return discard_pos + 1, 1 if is_riichi else 0
            else:
                print(f"⚠️ 手牌数量错误: {len(m_pais)}张，期望14张")
                return 1, 0
        except Exception as e:
            print(f"❌ JanQcore出牌决策失败: {e}")
            return 1, 0
    
    def load_statistics(self) -> List[List[int]]:
        try:
            if os.path.exists(self.stats_file_path):
                with open(self.stats_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    print(f"📊 加载统计数据: {self.stats_file_path}")
                    return data
            else:
                new_stats = [[0 for _ in range(34)] for _ in range(7)]
                print(f"📊 创建新的统计数据")
                return new_stats
        except Exception as e:
            print(f"❌ 加载统计数据失败: {e}")
            return [[0 for _ in range(34)] for _ in range(7)]
    
    def save_statistics(self):
        try:
            with open(self.stats_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.statistics, f, indent=2, ensure_ascii=False)
            print(f"💾 统计数据已保存: {self.stats_file_path}")
        except Exception as e:
            print(f"❌ 保存统计数据失败: {e}")
    
    def update_statistics(self, area: int, tile_number: int):
        try:
            if area < 1 or area > 7:
                print(f"⚠️ 无效的区域编号: {area}")
                return
            if tile_number < 0 or tile_number > 33:
                print(f"⚠️ 无效的牌编号: {tile_number}")
                return
            self.statistics[area - 1][tile_number] += 1
            print(f"📊 更新统计: 区域{area} -> 牌{tile_number} (当前次数: {self.statistics[area - 1][tile_number]})")
            self.save_statistics()
        except Exception as e:
            print(f"❌ 更新统计数据失败: {e}")
    
    def get_latest_nyukyu_black_list(self) -> List[int]:
        try:
            if not os.path.exists(self.log_file_path):
                return []
            with open(self.log_file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for line in reversed(lines):
                if "SetNyukyuBlack called with list =" in line:
                    if "list = []" in line:
                        print("📋 获取到最新入球黑名单: [] (空列表)")
                        return []
                    match = re.search(r'list = \[(.*)\]', line)
                    if match:
                        black_list_str = match.group(1)
                        if black_list_str.strip() == "":
                            print("📋 获取到最新入球黑名单: [] (空列表)")
                            return []
                        black_list = [int(x.strip()) for x in black_list_str.split(',') if x.strip()]
                        print(f"📋 获取到最新入球黑名单: {black_list}")
                        return black_list
            print("⚠️ 未找到SetNyukyuBlack信息")
            return []
        except Exception as e:
            print(f"❌ 获取入球黑名单失败: {e}")
            return []
    
    def update_tenpai_list(self):
        try:
            if not os.path.exists(self.log_file_path):
                return
            with open(self.log_file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if len(lines) >= 2:
                second_last_line = lines[-2].strip()
                if "SetNyukyuBlack called with list =" in second_last_line:
                    match = re.search(r'list = \[(.*)\]', second_last_line)
                    if match:
                        tenpai_list_str = match.group(1)
                        tenpai_list = [int(x.strip()) for x in tenpai_list_str.split(',')]
                        self.challenge_bonus_tenpai_list = tenpai_list
                        print(f"📋 更新进张列表: {tenpai_list}")
                        return
            print(f"📋 保持原有进张列表: {self.challenge_bonus_tenpai_list}")
        except Exception as e:
            print(f"❌ 更新进张列表失败: {e}")
    
    def determine_shoot_area_richi(self, m_pais: List[int], m_balls: int, tenpai_list: List[int]) -> tuple:
        try:
            m_pais = [pai for pai in m_pais if pai != 9999]
            if len(m_pais) == 13:
                shoot_area = JanQcore(m_pais, m_balls)
                return shoot_area, 0.0
            else:
                print(f"⚠️ 手牌数量错误: {len(m_pais)}张，期望13张")
                print(m_pais)
                return 1, 0.0
        except Exception as e:
            print(f"❌ JanQcore决策失败: {e}")
            return 1, 0.0
    
    def determine_shoot_area(self, m_pais: List[int], m_balls: int, m_shanten_num: int) -> tuple:
        try:
            m_pais = [pai for pai in m_pais if pai != 9999]
            if len(m_pais) == 13:
                shoot_area = JanQcore(m_pais, m_balls)
                return shoot_area, 0.0
            else:
                print(f"⚠️ 手牌数量错误: {len(m_pais)}张，期望13张")
                print(m_pais)
                return 1, 0.0
        except Exception as e:
            print(f"❌ JanQcore决策失败: {e}")
            return 1, 0.0
    
    def handle_bet_wait_normal(self, state_data: Dict):
        print("🎯 状态: BetWait Normal - 按下BET按钮")
        try:
            f(1)
            print("✅ BET按钮按下完成")
        except Exception as e:
            print(f"❌ BET按钮按下失败: {e}")
    
    def handle_shoot_wait_normal(self, state_data: Dict):
        print("🎯 状态: ShootWait Normal - 分析手牌并确定射击区域")
        m_pais = state_data.get('mPais', [])
        m_shanten_num = state_data.get('mShantenNum', -1)
        m_balls = state_data.get('mBalls', -1)
        m_isReach = state_data.get('mIsReach', 0)
        self.update_tenpai_list()
        if m_isReach == 1:
            shoot_area, shoot_score = self.determine_shoot_area_richi(m_pais, m_balls, self.challenge_bonus_tenpai_list)
        else:    
            shoot_area, shoot_score = self.determine_shoot_area(m_pais, m_balls, m_shanten_num)
        print(f"🎯 确定射击区域: {shoot_area}")
        try:
            f(shoot_area)
            print(f"✅ 区域 {shoot_area} 按下完成")
        except Exception as e:
            print(f"❌ 区域 {shoot_area} 按下失败: {e}")
    
    def handle_ball_enter_normal(self, state_data: Dict):
        print("🎯 状态: BallEnter Normal - 统计区间-下球编号对应表")
        m_pais = state_data.get('mPais', [])
        m_shot_area = state_data.get('mShotArea', 0)
        valid_pais = [pai for pai in m_pais if pai != 9999]
        if valid_pais:
            last_tile = valid_pais[-1]
            print(f"📊 统计: 区域{m_shot_area} -> 下球编号{last_tile}")
            print(f"📊 完整手牌: {valid_pais}")
            self.update_statistics(m_shot_area, last_tile)
        else:
            print("⚠️ 无法获取最后一张牌")
    
    def handle_user_wait_normal(self, state_data: Dict):
        print("🎯 状态: UserWait Normal - 测试打出手牌功能")
        m_pais = state_data.get('mPais', [])
        m_dra_pai_id = state_data.get('mDraPaiId', 0)
        m_balls = state_data.get('mBalls', 0)
        m_ura_dra_pai_id = state_data.get('mUraDraPaiId', 0)
        m_shanten_num = state_data.get('mShantenNum', -1)
        valid_pais = [pai for pai in m_pais if pai != 9999]
        if valid_pais:
            last_tile = valid_pais[-1]
            print(f"🀄 摸到牌: {last_tile}")
            if m_dra_pai_id != 9999:
                print(f"🎴 宝牌编号: {m_dra_pai_id}")
            if m_ura_dra_pai_id != 9999:
                print(f"🎴 里宝牌编号: {m_ura_dra_pai_id}")
            print(f"📊 当前手牌: {valid_pais}")
            print(f"📊 手牌数量: {len(valid_pais)}")
            print(f"📊 向听数: {m_shanten_num}")
        else:
            print("⚠️ 未摸到有效牌")
        from press_areas import discard_tile
        print("\n🧪 开始测试各手牌选中分数:")
        print("=" * 50)
        try:
            valid_pais = [pai for pai in m_pais if pai != 9999]
            if len(valid_pais) == 14:
                discard_pos, is_win, is_riichi = JanQcore(valid_pais, m_balls)
                print(f"🎯 JanQcore决策结果:")
                print(f"   出牌位置: {discard_pos}")
                print(f"   是否胡牌: {is_win}")
                print(f"   是否立直: {is_riichi}")
                if is_win:
                    print("🎉 胡牌！")
                    f(1)
                else:
                    actual_discard_pos = discard_pos + 1 if discard_pos != 999 else 1
                    if is_riichi:
                        print("🎯 需要立直，先按立直按钮")
                        discard_tile(15, 1)
                    discard_tile(actual_discard_pos, 2)
                    print(f"✅ 出牌完成: 位置{actual_discard_pos}")
            else:
                print(f"⚠️ 手牌数量错误: {len(valid_pais)}张，期望14张")
        except Exception as e:
            print(f"❌ JanQcore决策失败: {e}")
            discard_pos, is_richii = self.get_discard_pos(m_pais, m_dra_pai_id, m_ura_dra_pai_id, m_balls)
            print(f"🎯 备选决策: 丢弃位置{discard_pos}, 是否立直{is_richii}")
            if is_richii == 1:
                discard_tile(15, 1)
            discard_tile(discard_pos, 2)
            print(f"✅ 出牌完成: 位置{discard_pos}")
    
    def handle_bet_wait_challenge(self, state_data: Dict):
        print("🎯 状态: BetWait Challenge - 按下BET按钮")
        try:
            f(1)
            print("✅ BET按钮按下完成")
        except Exception as e:
            print(f"❌ BET按钮按下失败: {e}")
    
    def handle_shoot_wait_challenge(self, state_data: Dict):
        print("🎯 状态: ShootWait Challenge - 分析听牌情况并确定射击区域")
        m_pais = state_data.get('mPais', [])
        m_balls = state_data.get('mBalls', -1)
        m_isReach = state_data.get('mIsReach', 0)
        if m_isReach != 1:
            print("❌ 立直状态错误")
            raise Exception("立直状态错误")
        self.update_tenpai_list()
        shoot_area, shoot_score = self.determine_shoot_area_richi(m_pais, m_balls, self.challenge_bonus_tenpai_list)
        print(f"🎯 确定射击区域: {shoot_area}")
        try:
            f(shoot_area)
            print(f"✅ 区域 {shoot_area} 按下完成")
        except Exception as e:
            print(f"❌ 区域 {shoot_area} 按下失败: {e}")
    
    def handle_ball_enter_challenge(self, state_data: Dict):
        print("🎯 状态: BallEnter Challenge - 统计区间-下球编号对应表")
        m_pais = state_data.get('mPais', [])
        m_shot_area = state_data.get('mShotArea', 0)
        valid_pais = [pai for pai in m_pais if pai != 9999]
        if valid_pais:
            last_tile = valid_pais[-1]
            print(f"📊 统计: 区域{m_shot_area} -> 下球编号{last_tile}")
            print(f"📊 完整手牌: {valid_pais}")
            self.update_statistics(m_shot_area, last_tile)
        else:
            print("⚠️ 无法获取最后一张牌")
    
    def handle_bet_wait_bonus(self, state_data: Dict):
        print("🎯 状态: BetWait Bonus - 按下BET按钮")
        try:
            f(1)
            print("✅ BET按钮按下完成")
        except Exception as e:
            print(f"❌ BET按钮按下失败: {e}")
    
    def handle_shoot_wait_bonus(self, state_data: Dict):
        print("🎯 状态: ShootWait Bonus - 分析听牌情况并确定射击区域")
        m_pais = state_data.get('mPais', [])
        m_balls = state_data.get('mBalls', -1)
        m_isReach = state_data.get('mIsReach', 0)
        if m_isReach != 1:
            print("❌ 立直状态错误")
            raise Exception("立直状态错误")
        self.update_tenpai_list()
        shoot_area, shoot_score = self.determine_shoot_area_richi(m_pais, m_balls, self.challenge_bonus_tenpai_list)
        print(f"🎯 确定射击区域: {shoot_area}")
        try:
            f(shoot_area)
            print(f"✅ 区域 {shoot_area} 按下完成")
        except Exception as e:
            print(f"❌ 区域 {shoot_area} 按下失败: {e}")
    
    def process_game_state(self, state_data: Dict):
        state = state_data.get('state', '')
        phrase = state_data.get('phrase', '')
        self.game_state = state_data
        print(f"\n🔄 处理状态: {state} | {phrase}")
        if state == "BetWait" and phrase == "Normal":
            self.handle_bet_wait_normal(state_data)
        elif state == "ShootWait" and phrase == "Normal":
            self.handle_shoot_wait_normal(state_data)
        elif state == "BallEnter" and phrase == "Normal":
            self.handle_ball_enter_normal(state_data)
        elif state == "UserWait" and phrase == "Normal":
            self.handle_user_wait_normal(state_data)
        elif state == "BetWait" and phrase == "Challenge":
            self.handle_bet_wait_challenge(state_data)
        elif state == "ShootWait" and phrase == "Challenge":
            self.handle_shoot_wait_challenge(state_data)
        elif state == "BallEnter" and phrase == "Challenge":
            self.handle_ball_enter_challenge(state_data)
        elif state == "BetWait" and phrase == "Bonus":
            self.handle_bet_wait_bonus(state_data)
        elif state == "ShootWait" and phrase == "Bonus":
            self.handle_shoot_wait_bonus(state_data)
        else:
            print(f"ℹ️ 未处理的状态: {state} | {phrase}")
    
    def run(self):
        print("🚀 开始游戏自动化监控...")
        print("按 Ctrl+C 停止程序")
        try:
            while True:
                new_lines = self.read_log_file()
                for line in new_lines:
                    line = line.strip()
                    if line:
                        state_data = self.parse_log_line(line)
                        if state_data:
                            self.process_game_state(state_data)
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n⏹️ 程序已停止")
        except Exception as e:
            print(f"❌ 程序运行出错: {e}")

def main():
    log_file_path = "C:\Program Files (x86)\SEGA\sega_net_MJ\MJ\BepInEx\LogOutput.log"
    automation = GameAutomation(log_file_path)
    automation.run()

if __name__ == "__main__":
    main()