# JanQ游戏自动化配置文件

# 游戏窗口配置
GAME_WINDOW_TITLE = "セガNET麻雀MJ"
GAME_RESOLUTION = (1141, 645)
GAME_WINDOW_COORDS = {
    'left_top': (1035, 244),
    'right_bottom': (2176, 889),
    'width': 1141,
    'height': 645
}

# 时间配置（毫秒）
BUTTON_PRESS_PRECISION = 1
DEFAULT_BET_TIME = 1000

# 执行模式配置
USE_CPP_ACTUATOR = True
ACTUATOR_PATH = "actuator.exe"

# 游戏区域配置
AREA_PRESS_TIMES = {
    1: 300, 2: 400, 3: 600, 4: 685, 5: 800, 6: 1000, 7: 1300
}

# 按压按钮相对位置配置
BUTTON_RELATIVE_OFFSET = {
    'right_offset': -0.07,
    'bottom_offset': -0.10
}

# 麻将牌区域配置
TILE_REGIONS = {
    "hand_tiles": {
        'left_top': (1121, 775),
        'right_bottom': (1857, 851),
        'width': 736,
        'height': 76,
        'relative_left': 10,
        'relative_top': 82.4,
        'relative_width': 64.5,
        'relative_height': 11.8
    },
    "drawn_tile": {
        'left_top': (1887, 775),
        'right_bottom': (1942, 851),
        'width': 55,
        'height': 76,
        'relative_left': 74.7,
        'relative_top': 82.4,
        'relative_width': 4.8,
        'relative_height': 11.8
    },
    "dora_tile": {
        'left_top': (2070, 478),
        'right_bottom': (2104, 526),
        'width': 34,
        'height': 48,
        'relative_left': 90.7,
        'relative_top': 36.3,
        'relative_width': 3.0,
        'relative_height': 7.4
    },
    "uradora_tile": {
        'left_top': (2115, 478),
        'right_bottom': (2149, 526),
        'width': 34,
        'height': 48,
        'relative_left': 94.7,
        'relative_top': 36.3,
        'relative_width': 3.0,
        'relative_height': 7.4
    },
    "riichi_position": {
        'x': 2104,
        'y': 617,
        'relative_x': 93.7,
        'relative_y': 57.8
    }
}

# 下注按钮位置
BET_BUTTON_POSITION = None

# 闪烁动画监测区域
FLASH_ANIMATION_REGION = (200, 100, 600, 200)

# 状态文字区域
STATUS_TEXT_REGIONS = {
    "game_status": (50, 50, 300, 100),
    "balance": (700, 50, 300, 100),
    "bet_amount": (400, 50, 200, 100),
}

# 麻将牌模板配置
TILE_TEMPLATE_SIZE = (40, 56)
TILE_TEMPLATE_PATH = "assets/tiles/"

# 图像识别配置
MATCHING_THRESHOLD = 0.95

# 游戏自动化配置
GAME_LOG_FILE_PATH = "C:\Program Files (x86)\SEGA\sega_net_MJ\MJ\BepInEx\LogOutput.log"
LOG_MONITOR_INTERVAL = 0.1

# 射击区域策略配置
SHOOT_STRATEGY = {
    'low_shanten': 4,
    'medium_shanten': 3,
    'high_shanten': 1,
    'default_area': 4
}

# BET按钮配置
BET_BUTTON_AREA = 1

# 打出手牌配置
DISCARD_TILE_CONFIG = {
    'click_interval': 0,
    'single_click_duration': 30,
    'double_click_duration': 30,
    'hand_tiles_count': 13,
    'tile_width': 56.6,
    'tile_center_offset': 28.3,
    'drawn_tile_index': 14,
    'riichi_tile_index': 15
}

# 麻将牌索引映射: 0-8万, 9-17索, 18-26饼, 27-33字牌

probs = [
    [13, 11, 12, 10, 9, 8, 7, 6, 4, 0,0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,0, 20,0,0,0,0,0,0],
    [0,0,2.85,4.275,8.55,9.975,12.825,9.975,8.55, 3,3.5,4.5,3.5,3,1.5,1,0,0, 0,0,0,0,0,0,0,0,0, 3,20,0,0,0,0,0],
    [0,0,1,1.5,3,3.5,4.5,3.5,3, 9,10.5,13.5,10.5,9,4.5,3,0,0, 0,0,0,0,0,0,0,0,0, 0,20,0,0,0,0,0],
    [0,0,0.2,0.3,0.6,0.7,0.9,0.7,0.6, 1.8,2.1,3.3,3,3.6,3,3.3,2.1,1.8, 0.6,0.7,0.9,0.7,0.6,0.3,0.2,0,0, 0,4,4,0,20,20,20],
    [0,0,0,0,0,0,0,0,0, 0,0,3,4.5,9,10.5,13.5,10.5,9, 3,3.5,4.5,3.5,3,1.5,1,0,0, 0,0,20,0,0,0,0],
    [0,0,0,0,0,0,0,0,0, 0,0,1,1.5,3,3.5,4.5,3.5,3, 8.55,9.975,12.825,9.975,8.55,4.275,2.85,0,0, 0,0,20,3,0,0,0],
    [0,0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,0, 4,6,7,8,9,10,12,11,13, 0,0,0,20,0,0,0]
]