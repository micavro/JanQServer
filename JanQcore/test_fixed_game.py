#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import random
from typing import List, Tuple, Optional
from dataclasses import dataclass
import decision

@dataclass
class GameStats:
    """游戏统计信息"""
    total_games: int = 0
    win_games: int = 0
    no_result_games: int = 0

class MahjongGame:
    """麻将游戏主类"""
    
    def __init__(self):
        self.stats = GameStats()
    
    def user_decision_1(self, hand_13: List[int], remaining_draws: int) -> int:
        """
        用户决策函数1：根据给定的13张牌和剩余自摸数量，返回1-7中的一个数字
        
        Args:
            hand_13: 13张手牌，用0-33的数字表示
            remaining_draws: 剩余自摸数量
            
        Returns:
            1-7中的一个数字
        """
        return decision.decision_13(hand_13, remaining_draws)
    
    def user_decision_2(self, hand_14: List[int], remaining_draws: int) -> Tuple[bool, int, bool]:
        """
        用户决策函数2：根据给定的14张牌和剩余自摸数量，做出胡牌/打牌的判断
        
        Args:
            hand_14: 14张手牌，用0-33的数字表示
            remaining_draws: 剩余自摸数量
            
        Returns:
            (是否胡牌, 要打的牌的数字0-33，胡牌时返回-1, 是否立直)
        """
        return decision.decision_14(hand_14, remaining_draws)
    
    def draw_card_by_probability(self, choice: int) -> int:
        """
        根据用户选择的概率表抽取一张牌
        使用config.py中的probs概率表
        
        Args:
            choice: 1-7的选择（对应probs数组的索引0-6）
            
        Returns:
            抽取的牌（0-33的数字）
        """
        # 从config.py导入的概率表
        probs = [
            # 1区
            [13, 11, 12, 10, 9, 8, 7, 6, 4,   # 万1-9
             0,0,0,0,0,0,0,0,0,               # 索1-9
             0,0,0,0,0,0,0,0,0,               # 饼1-9
             20,0,0,0,0,0,0],                 # 东南西北白发中

            # 2区
            [0,0,2.85,4.275,8.55,9.975,12.825,9.975,8.55,  # 万
             3,3.5,4.5,3.5,3,1.5,1,0,0,                   # 索
             0,0,0,0,0,0,0,0,0,                           # 饼
             3,20,0,0,0,0,0],                             # 东南西北白发中

            # 3区
            [0,0,1,1.5,3,3.5,4.5,3.5,3,                   # 万
             9,10.5,13.5,10.5,9,4.5,3,0,0,                # 索
             0,0,0,0,0,0,0,0,0,                           # 饼
             0,20,0,0,0,0,0],                             # 东南西北白发中

            # 4区
            [0,0,0.2,0.3,0.6,0.7,0.9,0.7,0.6,             # 万
             1.8,2.1,3.3,3,3.6,3,3.3,2.1,1.8,             # 索
             0.6,0.7,0.9,0.7,0.6,0.3,0.2,0,0,             # 饼
             0,4,4,0,20,20,20],                           # 东南西北白发中

            # 5区
            [0,0,0,0,0,0,0,0,0,                           # 万
             0,0,3,4.5,9,10.5,13.5,10.5,9,                # 索 (注意：从3开始)
             3,3.5,4.5,3.5,3,1.5,1,0,0,                   # 饼
             0,0,20,0,0,0,0],                             # 东南西北白发中

            # 6区
            [0,0,0,0,0,0,0,0,0,                           # 万
             0,0,1,1.5,3,3.5,4.5,3.5,3,                   # 索
             8.55,9.975,12.825,9.975,8.55,4.275,2.85,0,0, # 饼
             0,0,20,3,0,0,0],                             # 东南西北白发中

            # 7区
            [0,0,0,0,0,0,0,0,0,                           # 万
             0,0,0,0,0,0,0,0,0,                           # 索
             4,6,7,8,9,10,12,11,13,                       # 饼
             0,0,0,20,0,0,0]                              # 东南西北白发中
        ]
        
        # 确保选择在有效范围内
        if choice < 1 or choice > 7:
            choice = 1  # 默认使用1区
        
        # 获取对应区域的概率分布（choice-1因为数组索引从0开始）
        probabilities = probs[choice - 1]
        
        # 使用random.choices根据概率选择牌
        # 牌号从0到33（34张牌）
        card = random.choices(range(34), weights=probabilities)[0]
        
        return card
    
    def generate_initial_hand(self) -> List[int]:
        """生成初始13张手牌，确保每种牌最多4张"""
        hand = []
        while len(hand) < 13:
            card = random.randint(0, 33)
            if hand.count(card) < 4:
                hand.append(card)
        return hand
    
    def play_single_game(self) -> str:
        """
        进行一局游戏
        
        Returns:
            游戏结果：'win'（胡牌）或'no_result'（没结果）
        """
        # 初始化游戏
        hand = self.generate_initial_hand()
        remaining_draws = 8
        
        while remaining_draws > 0:
            print(sorted(hand))
            choice = self.user_decision_1(hand, remaining_draws)
            while True:
                new_card = self.draw_card_by_probability(choice)
                card_count = hand.count(new_card)
                if card_count >= 4:
                    continue
                elif card_count == 3:
                    hand.append(new_card)
                    break
                else:
                    hand.append(new_card)
                    remaining_draws -= 1
                    break
            is_win, discard_card, is_riichi = self.user_decision_2(hand, remaining_draws)
            if is_win:
                return 'win'
            else:
                hand.remove(discard_card)
        return 'no_result'
    
    def play_multiple_games(self, num_games: int):
        """进行多局游戏"""
        print(f"开始进行 {num_games} 局游戏...")
        
        for game_num in range(1, num_games + 1):
            result = self.play_single_game()
            if result == 'win':
                self.stats.win_games += 1
                print(f"第 {game_num} 局: 胡牌")
            else:
                self.stats.no_result_games += 1
                print(f"第 {game_num} 局: 没结果")
            self.stats.total_games += 1
            win_rate = (self.stats.win_games / self.stats.total_games) * 100
            print(f"当前统计: {self.stats.total_games}局, 胡牌{self.stats.win_games}局, 胡牌率{win_rate:.1f}%")
    
    def print_final_stats(self):
        """打印最终统计信息"""
        print("\n" + "=" * 50)
        print("游戏统计结果:")
        print(f"总局数: {self.stats.total_games}")
        print(f"胡牌局数: {self.stats.win_games}")
        print(f"没结果局数: {self.stats.no_result_games}")
        if self.stats.total_games > 0:
            win_rate = (self.stats.win_games / self.stats.total_games) * 100
            print(f"胡牌率: {win_rate:.2f}%")

def JanQcore(hand: List[int], remaining_draws: int):
    """
    JanQcore接口函数：自动判断牌数并返回相应结果
    
    Args:
        hand: 手牌列表，可以是13张或14张牌（0-33的数字）
        remaining_draws: 剩余自摸数量
        
    Returns:
        13张牌时: 击打区间1-7
        14张牌时: (打牌位置0-13, 是否胡牌, 是否立直)
    """
    if len(hand) == 13:
        # 13张牌的情况
        choice = decision.decision_13(hand, remaining_draws)
        return choice
        
    elif len(hand) == 14:
        # 14张牌的情况
        is_win, discard_card, is_riichi = decision.decision_14(hand, remaining_draws)
        
        if is_win:
            # 胡牌时返回位置0（表示胡牌）
            return 0, True, is_riichi
        else:
            # 找到要打的牌在14张牌中的位置
            for i, card in enumerate(hand):
                if card == discard_card:
                    return i, False, is_riichi
            # 如果找不到，返回999表示错误
            return 999, False, is_riichi
    else:
        # 牌数不正确
        raise ValueError(f"手牌数量必须是13张或14张，当前为{len(hand)}张")

def test_single_game():
    """测试单局游戏"""
    print("开始测试单局游戏...")
    game = MahjongGame()
    result = game.play_single_game()
    print(f"游戏结果: {result}")

if __name__ == "__main__":
    test_single_game()

