import subprocess
import time
import re
from tkinter import N
from typing import List, Tuple, Optional

def remove_ansi_codes(text: str) -> str:
    """移除ANSI颜色代码"""
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)

def majhong_num_to_string(hand: List[int]) -> str:
    """将手牌数字列表转换为麻将字符串格式"""
    man = []
    sou = []
    pin = []
    honor = []
    for card in hand:
        if 0 <= card <= 8:
            man.append(str(card + 1))
        elif 9 <= card <= 17:
            sou.append(str(card - 8))
        elif 18 <= card <= 26:
            pin.append(str(card - 17))
        elif 27 <= card <= 33:
            honor.append(str(card - 26))
    result = ""
    if man:
        result += "".join(sorted(man)) + "m"
    if sou:
        result += "".join(sorted(sou)) + "s"
    if pin:
        result += "".join(sorted(pin)) + "p"
    if honor:
        result += "".join(sorted(honor)) + "z"
    return result

def majhong_string_to_num(hand_string: str) -> List[int]:
    """将麻将字符串格式转换为手牌数字列表"""
    result = []
    import re
    man_matches = re.findall(r'(\d+)m', hand_string)
    for match in man_matches:
        for digit in match:
            num = int(digit) - 1
            if 0 <= num <= 8:
                result.append(num)
    sou_matches = re.findall(r'(\d+)s', hand_string)
    for match in sou_matches:
        for digit in match:
            num = int(digit) + 8
            if 9 <= num <= 17:
                result.append(num)
    pin_matches = re.findall(r'(\d+)p', hand_string)
    for match in pin_matches:
        for digit in match:
            num = int(digit) + 17
            if 18 <= num <= 26:
                result.append(num)
    honor_matches = re.findall(r'(\d+)z', hand_string)
    for match in honor_matches:
        for digit in match:
            num = int(digit) + 26
            if 27 <= num <= 33:
                result.append(num)
    return sorted(result)

def chinese_tile_to_num(tile_str: str) -> int:
    """将汉字牌名转换为数字"""
    chinese_numbers = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    honor_tiles = {'东': 1, '南': 2, '西': 3, '北': 4, '白': 5, '发': 6, '中': 7}
    if tile_str.endswith('万'):
        num = chinese_numbers.get(tile_str[:-1], 0)
        return num - 1 if 1 <= num <= 9 else -1
    elif tile_str.endswith('索'):
        num = chinese_numbers.get(tile_str[:-1], 0)
        return num + 8 if 1 <= num <= 9 else -1
    elif tile_str.endswith('饼'):
        num = chinese_numbers.get(tile_str[:-1], 0)
        return num + 17 if 1 <= num <= 9 else -1
    elif tile_str in honor_tiles:
        return honor_tiles[tile_str] + 26
    return -1

def majhong_num_to_34(hand: List[int]) -> List[int]:
    """将手牌数字列表转换为34位数量统计数组"""
    count_array = [0] * 34
    for card in hand:
        if 0 <= card <= 33:
            count_array[card] += 1
    return count_array

def majhong_34_to_num(count_array: List[int]) -> List[int]:
    """将34位数量统计数组转换为手牌数字列表"""
    hand = []
    for i, count in enumerate(count_array):
        for _ in range(count):
            hand.append(i)
    return hand

def call_mahjong_helper(hand_string: str) -> Tuple[int, str | dict]:
    """调用mahjong-helper.exe获取向听数信息"""
    try:
        cmd = f'chcp 65001 && mahjong-helper.exe {hand_string}'
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout = 60,
            encoding="utf-8"
        )
        output = result.stdout
        output = remove_ansi_codes(output)
        shanten = -2
        if "【已和牌】" in output:
            shanten = -1
#            print("OHHHHHHHHHHHHHHH胡牌！！！！！！")
            return -1, "胡牌"
        elif "当前听牌" in output:
            shanten = 0
        elif "切" in output:
            min_shanten_from_sections = 999
            for line in output.split('\n'):
                if "听牌：" in line:
                    min_shanten_from_sections = min(min_shanten_from_sections, 0)
                elif "一向听：" in line:
                    min_shanten_from_sections = min(min_shanten_from_sections, 1)
                elif "二向听：" in line or "两向听：" in line:
                    min_shanten_from_sections = min(min_shanten_from_sections, 2)
                elif "三向听：" in line:
                    min_shanten_from_sections = min(min_shanten_from_sections, 3)
                elif "四向听：" in line:
                    min_shanten_from_sections = min(min_shanten_from_sections, 4)
                elif "五向听：" in line:
                    min_shanten_from_sections = min(min_shanten_from_sections, 5)
                elif "六向听：" in line:
                    min_shanten_from_sections = min(min_shanten_from_sections, 6)
            if min_shanten_from_sections != 999:
                shanten = min_shanten_from_sections
        else:
            chinese_numbers = {
                '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7
            }
            pattern = r"当前([一二两三四五六七])向听"
            match = re.search(pattern, output)
            if match:
                chinese_char = match.group(1)
                shanten = chinese_numbers.get(chinese_char, -1)
        if shanten == -2:
            print(hand_string)
            print(output)
            print(f"无法找到向听数模式，原始输出: {repr(output)}")
            return -1, "无法解析向听数"
        lines = output.strip().split('\n')
        if "切" in output:
            discard_info = {}
            min_shanten = 999
            current_section_shanten = 999
            for line in lines:
                if "听牌：" in line:
                    current_section_shanten = 0
                elif "一向听：" in line:
                    current_section_shanten = 1
                elif "二向听：" in line or "两向听：" in line:
                    current_section_shanten = 2
                elif "三向听：" in line:
                    current_section_shanten = 3
                elif "四向听：" in line:
                    current_section_shanten = 4
                elif "五向听：" in line:
                    current_section_shanten = 5
                elif "六向听：" in line:
                    current_section_shanten = 6
                elif "切" in line and "=>" in line:
                    discard_match = re.search(r'切\s*([一二三四五六七八九东南西北白发中]+[mspz]?|[一二三四五六七八九\d]+[万索饼]|\d+[mspz])', line)
                    tiles_match = re.search(r'\[([^\]]+)\]$', line)
                    if discard_match and tiles_match:
                        discard_tile_raw = discard_match.group(1)
                        tiles_info = tiles_match.group(1)
                        if discard_tile_raw.isdigit() or any(c in discard_tile_raw for c in 'mspz'):
                            discard_tile_str = discard_tile_raw
                        elif discard_tile_raw.endswith('万'):
                            discard_tile_str = discard_tile_raw.replace('万', 'm')
                        elif discard_tile_raw.endswith('索'):
                            discard_tile_str = discard_tile_raw.replace('索', 's')
                        elif discard_tile_raw.endswith('饼'):
                            discard_tile_str = discard_tile_raw.replace('饼', 'p')
                        else:
                            discard_tile_num = chinese_tile_to_num(discard_tile_raw)
                            if discard_tile_num != -1:
                                discard_tile_str = majhong_num_to_string([discard_tile_num])
                            else:
                                continue
                        if current_section_shanten < min_shanten:
                            discard_info = {}
                            min_shanten = current_section_shanten
                        if current_section_shanten == min_shanten:
                            discard_info[discard_tile_str] = tiles_info
            return min_shanten, discard_info
        else:
            last_line = lines[-1] if lines else ""
            bracket_pattern = r"\[([^\]]+)\]"
            bracket_matches = re.findall(bracket_pattern, last_line)
            if bracket_matches:
                tiles_info = bracket_matches[-1]
            else:
                tiles_info = ""
            return shanten, tiles_info
    except subprocess.TimeoutExpired:
        print("mahjong-helper.exe 执行超时")
        return -1, "执行超时"
    except FileNotFoundError:
        print("未找到 mahjong-helper.exe 文件")
        return -1, "文件未找到"
    except Exception as e:
        print(f"调用 mahjong-helper.exe 时出错: {e}")
        import traceback
        traceback.print_exc()
        return -1, str(e)

def decision_13(hand_13: List[int], remaining_draws: int) -> int:
    """根据13张手牌和剩余自摸数量，返回1-7中的一个数字"""
    hand_string = majhong_num_to_string(hand_13)
    shanten, tiles_info = call_mahjong_helper(hand_string)
    tiles_num = majhong_string_to_num(tiles_info)
    update_tiles_34 = majhong_num_to_34(tiles_num)
    ori_tiles_num = majhong_string_to_num(hand_string)
    ori_tile_34 = majhong_num_to_34(ori_tiles_num)

    if shanten == 0:
        pass
    from config import probs
    probs_now = probs
    for tile_idx in range(34):
        if ori_tile_34[tile_idx] >= 3:
            for area_idx in range(7):
                probs_now[area_idx][tile_idx] = 0  #被保护的tile将不计算概率
    
    area_scores = [0] * 7
    
    # 1. 混一色特殊检查
########################################################
    man_count, sou_count, pin_count = 0, 0, 0
    for tile_idx in range(34):
        num = ori_tile_34[tile_idx]
        if 0 <= tile_idx <= 8:
            man_count += num
        elif 9 <= tile_idx <= 17:
            sou_count += num
        elif 18 <= tile_idx <= 26:
            pin_count += num
        elif tile_idx == 27:    # 东
            man_count += num
            if num >= 2:
                sou_count += num
                pin_count += num
        elif tile_idx == 28:    # 南
            man_count += 0.5 * num
            sou_count += 0.5 * num
            if num >= 2:
                pin_count += num
        elif tile_idx == 29:    # 西
            sou_count += 0.5 * num
            pin_count += 0.5 * num
            if num >= 2:
                man_count += num
        elif tile_idx == 30:    # 北
            pin_count += num
            if num >= 2:
                man_count += num
                sou_count += num
        elif 31 <= tile_idx <=33:    # 白 发 中
            sou_count += num
            if num >= 2:
                man_count += num
                pin_count += num
    man_count += remaining_draws
    sou_count += remaining_draws * 0.8
    pin_count += remaining_draws
    flag = 0
    if man_count >= 13.5:
        area_scores[0] += 10
        area_scores[1] += 10
        for tile_idx in range(27,34):
            if ori_tile_34[tile_idx] >= 2:
                update_tiles_34[tile_idx] = 1
                if 31 <= tile_idx <= 33:
                    flag = 1
    if sou_count >= 14:
        area_scores[2] += 10
        area_scores[3] += 10
        area_scores[4] += 10
        for tile_idx in range(27,34):
            if ori_tile_34[tile_idx] >= 2:
                update_tiles_34[tile_idx] = 1
    if pin_count >= 13.5:
        area_scores[5] += 10
        area_scores[6] += 10
        for tile_idx in range(27,34):
            if ori_tile_34[tile_idx] >= 2:
                update_tiles_34[tile_idx] = 1
                if 31 <= tile_idx <= 33:
                    flag = 1
    area_scores[3] += flag * (10)    
########################################################
    normalized_probs = []
    for area_probs in probs_now:
        total = sum(area_probs)
        if total > 0:
            normalized_area = [p / total for p in area_probs]
        else:
            normalized_area = [0] * 34
        normalized_probs.append(normalized_area)
    for area_idx, area_probs in enumerate(normalized_probs):
        score = 0
        for tile_idx in range(34):
            score += area_probs[tile_idx] * update_tiles_34[tile_idx]
        area_scores[area_idx] += score


    print("向听数：", shanten, majhong_num_to_string(hand_13), "剩余：", remaining_draws)
    print( [round(score, 4) for score in area_scores])
    best_area = area_scores.index(max(area_scores)) + 1
    if area_scores[best_area - 1] >= 10:
        print("混一色GO!")
    return best_area

    

def decision_14(hand_14: List[int], remaining_draws: int) -> Tuple[bool, int, bool]:
    """
    根据14张手牌和剩余自摸数量，做出胡牌/打牌的判断
    
    Args:
        hand_14: 14张手牌
        remaining_draws: 剩余自摸数量
        
    Returns:
        (是否胡牌, 要打的牌的数字, 是否立直)
    """
    hand_string = majhong_num_to_string(hand_14)
    shanten, detail = call_mahjong_helper(hand_string)
    ori_tile_34 = majhong_num_to_34(hand_14)

    # 检查是否立直（向听数=0）
    is_riichi = (shanten == 0)
    
    if shanten == -1:
        return True, -1, is_riichi

    man_count, sou_count, pin_count = 0, 0, 0
    for tile_idx in range(34):
        num = ori_tile_34[tile_idx]
        if 0 <= tile_idx <= 8:
            man_count += num
        elif 9 <= tile_idx <= 17:
            sou_count += num
        elif 18 <= tile_idx <= 26:
            pin_count += num
        elif tile_idx == 27:    # 东
            man_count += num
            if num >= 2:
                sou_count += num
                pin_count += num
        elif tile_idx == 28:    # 南
            man_count += 0.5 * num
            sou_count += 0.5 * num
            if num >= 2:
                pin_count += num
        elif tile_idx == 29:    # 西
            sou_count += 0.5 * num
            pin_count += 0.5 * num
            if num >= 2:
                man_count += num
        elif tile_idx == 30:    # 北
            pin_count += num
            if num >= 2:
                man_count += num
                sou_count += num
        elif 31 <= tile_idx <=33:    # 白 发 中
            sou_count += num
            if num >= 2:
                man_count += num
                pin_count += num
    man_count += remaining_draws
    sou_count += remaining_draws * 0.8
    pin_count += remaining_draws

    ########################################################
    if man_count >= 14:
        # 1. 检查手牌是否有非万字的数牌
        # 2. 检查手牌是否有非东的单张字牌
        # 3. 完全根据牌效率打
        pass
    elif pin_count >= 14:
        # 1. 检查手牌是否有非饼字的数牌
        # 2. 检查手牌是否有非北的单张字牌
        # 3. 完全根据牌效率打
        pass
    elif sou_count >= 14:
        # 1. 检查手牌是否有非索字的数牌
        # 2. 检查手牌是否有非南的单张字牌
        # 3. 完全根据牌效率打
        pass
    
    
    if isinstance(detail, dict) and detail:
        from config import probs
        probs_now = probs
        for tile_idx in range(34):
            if ori_tile_34[tile_idx] >= 3:
                for area_idx in range(7):
                    probs_now[area_idx][tile_idx] = 0  #被保护的tile将不计算概率
        normalized_probs = []
        for area_probs in probs_now:
            total = sum(area_probs)
            if total > 0:
                normalized_area = [p / total for p in area_probs]
            else:
                normalized_area = [0] * 34
            normalized_probs.append(normalized_area)
        best_discard_score = -1
        best_discard_card = None
        for discard_tile, tiles_info in detail.items():
            tiles_num = majhong_string_to_num(tiles_info)
            tiles_34 = majhong_num_to_34(tiles_num)
            area_scores = []
            for area_idx, area_probs in enumerate(normalized_probs):
                score = 0
                for tile_idx in range(34):
                    score += area_probs[tile_idx] * tiles_34[tile_idx]
                area_scores.append(score)
            max_score = max(area_scores)
            if max_score > best_discard_score:
                best_discard_score = max_score
                best_discard_card = discard_tile
        if best_discard_card:
            discard_num = majhong_string_to_num(best_discard_card)[0]
            return False, discard_num, is_riichi
    hand_count = {}
    for card in hand_14:
        hand_count[card] = hand_count.get(card, 0) + 1
    discard_candidates = []
    for card, count in hand_count.items():
        if count >= 2:
            continue
        discard_candidates.append(card)
    if discard_candidates:
        discard_card = discard_candidates[0]
    else:
        max_count = max(hand_count.values())
        cards_to_discard = [card for card, count in hand_count.items() if count == max_count]
        discard_card = cards_to_discard[0]
    for i, card in enumerate(hand_14):
        if card == discard_card:
            return False, i, is_riichi


###################################TEST###################################
if __name__ == "__main__":
    test_hand = [0, 4, 7, 10, 11, 16, 19, 21, 23, 26, 31, 32, 33]
    mahjong_str = majhong_num_to_string(test_hand)
    print(f"测试手牌: {test_hand}")
    print(f"转换结果: {mahjong_str}")
    converted_back = majhong_string_to_num(mahjong_str)
    print(f"反向转换结果: {converted_back}")
    print(f"转换是否正确: {sorted(test_hand) == converted_back}")
    tiles_34 = majhong_num_to_34(test_hand)
    # 测试call_mahjong_helper
    shanten, tiles_info = call_mahjong_helper(mahjong_str)
    print(f"向听数: {shanten}, 进张信息: {tiles_info}")
    # 测试决策函数
    choice = decision_13(test_hand, 5)
    print(f"13张决策结果: {choice}")
    # 测试14张决策
    test_hand_14 = [2, 2, 4, 10, 10, 12, 13, 15, 16, 17, 20, 22, 26, 30]
    mahjong_str_2 = majhong_num_to_string(test_hand_14)
    print(f"测试手牌: {test_hand_14}")
    print(f"转换结果: {mahjong_str_2}")
    shanten_2, tiles_info_2 = call_mahjong_helper(mahjong_str_2)
    print(f"向听数: {shanten_2}, 进张信息: {tiles_info_2}")
    result_14 = decision_14(test_hand_14, 5)
    print(f"14张决策结果: {result_14}")
