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
    
    def user_decision_2(self, hand_14: List[int], remaining_draws: int) -> Tuple[bool, int]:
        """
        用户决策函数2：根据给定的14张牌和剩余自摸数量，做出胡牌/打牌的判断
        
        Args:
            hand_14: 14张手牌，用0-33的数字表示
            remaining_draws: 剩余自摸数量
            
        Returns:
            (是否胡牌, 要打的牌的数字0-33，胡牌时返回-1)
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
        
        print(f"初始手牌: {sorted(hand)}")
        
        while remaining_draws > 0:
            print(f"当前手牌: {sorted(hand)}, 剩余自摸: {remaining_draws}")
            
            # 13张牌决策
            choice = self.user_decision_1(hand, remaining_draws)
            print(f"选择区域: {choice}")
            
            # 抽牌
            while True:
                new_card = self.draw_card_by_probability(choice)
                card_count = hand.count(new_card)
                print(f"抽到牌: {new_card}, 当前手牌中此牌数量: {card_count}")
                
                if card_count >= 4:
                    print("此牌已满4张，重新抽牌")
                    continue
                elif card_count == 3:
                    hand.append(new_card)
                    print("此牌已有3张，直接加入手牌（不减少自摸次数）")
                    break
                else:
                    hand.append(new_card)
                    remaining_draws -= 1
                    print(f"加入手牌，剩余自摸次数: {remaining_draws}")
                    break
            
            print(f"14张手牌: {sorted(hand)}")
            
            # 14张牌决策
            try:
                is_win, discard_card, is_riichi = self.user_decision_2(hand, remaining_draws)
                print(f"决策结果: 胡牌={is_win}, 打牌={discard_card}, 立直={is_riichi}")
                
                if is_win:
                    print("胡牌！")
                    return 'win'
                else:
                    # 移除要打的牌
                    if discard_card in hand:
                        hand.remove(discard_card)
                        print(f"打出牌: {discard_card}")
                    else:
                        print(f"错误：要打的牌 {discard_card} 不在手牌中")
                        return 'no_result'
                        
            except Exception as e:
                print(f"决策函数出错: {e}")
                return 'no_result'
        
        print("自摸次数用完，游戏结束")
        return 'no_result'

def test_single_game():
    """测试单局游戏"""
    print("开始测试单局游戏...")
    game = MahjongGame()
    result = game.play_single_game()
    print(f"游戏结果: {result}")

if __name__ == "__main__":
    test_single_game()

