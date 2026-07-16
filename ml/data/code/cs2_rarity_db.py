"""
CS2/CS:GO 皮肤稀有度数据库
============================
基于 CS2 游戏数据（items_game.txt）和社区维护的稀有度映射。
每个皮肤 finish（| 后面的名称）有固定的稀有度等级。

稀有度等级 (数值越大越稀有):
  1 - Consumer Grade (白色)  消费级
  2 - Industrial Grade (青色) 工业级
  3 - Mil-Spec      (蓝色)  军规级
  4 - Restricted    (紫色)  受限级
  5 - Classified    (粉色)  保密级
  6 - Covert        (红色)  隐秘级
  7 - Rare          (金色)  稀有 (刀/手套)
  8 - Contraband    (橙色)  违禁品 (M4A4 | Howl 独占)
"""

# ============================================================
# 已知皮肤 finish → rarity 映射
# 格式: "Skin Finish Name": rarity_level
# ============================================================

RARITY_MAP: dict[str, int] = {
    # ================================================================
    # Covert (6) — 隐秘 / 红色
    # ================================================================
    "Asiimov": 6,
    "Fire Serpent": 6,
    "Dragon Lore": 6,
    "Vulcan": 6,
    "Neon Revolution": 6,
    "Desolate Space": 6,
    "The Empress": 6,
    "Wild Lotus": 6,
    "Welcome to the Jungle": 6,
    "Bloodsport": 6,
    "Fuel Injector": 6,
    "Wasteland Rebel": 6,
    "Hyper Beast": 6,
    "Neo-Noir": 6,
    "Printstream": 6,
    "Eye of Horus": 6,
    "Panthera Onca": 6,
    "Oni Taiji": 6,
    "Gold Arabesque": 6,
    "Silk Tiger": 6,
    "X-Ray": 6,
    "Inline": 6,
    "Fade": 6,
    "Lightning Strike": 6,
    "Medusa": 6,
    "Poseidon": 6,
    "Icarus Fell": 6,
    "Hot Rod": 6,
    "Kill Confirmed": 6,
    "Battlescarred": 6,
    "Buzz Kill": 6,
    "Royal Paladin": 6,
    "Daybreak": 4,  # Restricted, 非 Covert
    "Flame Jormungandr": 6,
    "Emerald Jormungandr": 6,
    "Astral Jormungandr": 6,
    "Moonrise": 6,
    "Sunset Storm": 6,
    "Twilight Galaxy": 6,
    "Hydroponic": 6,
    "Golden Coil": 6,
    "Master Piece": 6,
    "Jaguar": 6,
    "Aquamarine Revenge": 6,
    "Knight": 6,  # M4A1-S | Knight
    "Gungnir": 6,
    "Prince": 6,
    "Emerald Dragon": 6,
    "Gold Arabesque": 6,  # AK-47 | Gold Arabesque (已移除武器前缀, 避免死代码)
    "The Traitor": 6,
    "Akihabara Accept": 6,
    "Howl": 8,  # M4A4 | Howl — 唯一 Contraband

    # ================================================================
    # Classified (5) — 保密 / 粉色
    # ================================================================
    "Redline": 5,
    "Red Laminate": 5,
    "Case Hardened": 5,
    "Atomic Alloy": 5,
    "Mecha Industries": 5,
    "Cyrex": 5,
    "Corticera": 5,
    "Bullet Rain": 5,
    "Graphite": 5,
    "Asiidustrial": 5,
    "Point Disarray": 5,
    "Electric Hive": 5,
    "Tigris": 5,
    "Orion": 5,
    "Muertos": 5,
    "Mortis": 5,
    "Worm God": 5,
    "Flashback": 5,
    "Disco Tech": 5,
    "Nitro": 5,
    "Royal Legion": 5,
    "Chromatic Aberration": 5,
    "Phantom Disruptor": 5,
    "Oceanic Threat": 5,
    "Monster Call": 5,
    "Power Loader": 5,
    "Brass": 5,
    "Dragon King": 5,
    "Man-o'-War": 5,
    "Chatterbox": 5,
    "Ivory": 5,
    "Avalanche": 5,
    "Whiteout": 5,
    "Emerald": 5,
    "Emerald Pinstripe": 4,  # AK-47 | Emerald Pinstripe, Dust 2 2021 Collection
    "Houndstooth": 5,
    "Orange Kimono": 5,
    "Vogue": 5,
    "Slaughter": 5,
    "Frontside Misty": 5,
    "Fever Dream": 5,  # AWP | Fever Dream, Spectrum Case
    "Cartel": 5,  # P250 | Cartel, Classified
    "USP-S | Kill Confirmed": 6,  # ← fix: this is Covert
    "USP-S | Royal Blue": 5,
    "M4A4 | The Coalition": 5,
    "M4A4 | Daybreak": 6,  # ← fix: Daybreak is Covert
    "M4A1-S | Mecha Industries": 5,
    "M4A4 | Desolate Space": 6,  # ← fix: Covert
    "AWP | Mortis": 5,
    "AWP | Fever Dream": 5,
    "Hand Cannon": 5,
    "Twilight": 5,
    "Chantico's Fire": 5,
    "Judgement of Anubis": 5,
    "Phosphor": 5,
    "Valence": 5,
    "Exoskeleton": 5,
    "Moon in Libra": 5,
    "Lady Justice": 5,
    "Traction": 5,
    "Leaded Glass": 4,  # Restricted
    "Candy Apple": 4,  # Restricted
    "Orange Peel": 4,  # Restricted
    "Dirt Drop": 4,  # Restricted

    # ================================================================
    # Restricted (4) — 受限 / 紫色
    # ================================================================
    "Dark Water": 4,
    "Guardian": 4,
    "Bone Mask": 4,
    "Heirloom": 4,
    "Pulse": 4,
    "Zirka": 4,
    "Serpent": 4,
    "Scorpion": 4,
    "Antique": 4,
    "Grinder": 4,
    "Conspiracy": 4,
    "Demeter": 4,
    "Lab Rats": 4,
    "Desert-Strike": 4,
    "Jungle Tiger": 4,
    "Toxic": 4,
    "Triarch": 4,
    "Black Nile": 4,
    "Wingshot": 4,
    "Ultralight": 4,
    "Surf": 4,
    "Fubar": 4,
    "Stylosa": 4,
    "Vino Primo": 4,
    "Decimator": 4,
    "Retribution": 4,
    "Abyssal Apparition": 4,
    "Temukau": 4,
    "Ice Coaled": 4,
    "Steel Delta": 4,
    "Amber Fade": 4,
    "Mehndi": 4,
    "Pinstripe": 4,
    "Pink DDPAT": 4,
    "Jet Set": 4,
    "Heat": 4,
    "Shattered": 4,
    "Royal": 4,
    "Briefing": 4,
    "Flower": 4,
    "Big Iron": 4,
    "Momentum": 4,
    "Viper": 4,
    "Riot": 4,
    "Hemoglobin": 4,
    "Water Sigil": 4,
    "Corinthian": 4,
    "Purple DDPAT": 4,
    "Anodized Air": 4,
    "Midnight": 4,
    "Orange Crash": 4,
    "Monkey Business": 4,
    "Hypnotic": 4,
    "Overgrowth": 4,
    "Meteorite": 4,
    "Fleet Flock": 4,
    "Triumvirate": 4,
    "Roadblock": 4,
    "Sienna Damask": 4,
    "Downtown": 4,
    "Metal Flowers": 4,
    "Blueprint": 4,
    "Necropos": 4,
    "Turbo Peek": 4,
    "Tread": 4,
    "Dragon Glass": 4,
    "Quadrangle": 4,
    "Backsplash": 4,
    "Crimson Web": 4,
    "Rapid Eye Movement": 4,
    "Cinquedea": 4,
    "Tom Cat": 4,
    "Phantom": 4,
    "Duality": 4,
    "Capillary": 4,
    "Blood Tiger": 4,
    "Crypsis": 4,
    "Undertow": 4,
    "Incinegator": 4,
    "Snack Attack": 4,

    # ================================================================
    # Mil-Spec (3) — 军规 / 蓝色
    # ================================================================
    "Blue Laminate": 4,  # Restricted, 非 Mil-Spec
    "Sand Dashed": 3,
    "Urban DDPAT": 3,
    "Forest DDPAT": 3,
    "Desert DDPAT": 3,
    "Jungle Dashed": 3,
    "VariCamo": 3,
    "Predator": 3,
    "Oxide Blaze": 3,
    "Elite Build": 3,
    "Bamboo Forest": 3,
    "Army Sheen": 3,
    "Army Mesh": 3,
    "Teclu Burner": 3,
    "Clear Polymer": 3,
    "Night Riot": 3,
    "Ironworks": 3,
    "Mosaico": 3,
    "The Bronze": 3,
    "Cracked Earth": 3,
    "Calicamo": 3,
    "Moroccan Mesh": 3,
    "Tornado": 3,
    "Runic": 3,
    "Pyre": 3,
    "Green Marine": 3,
    "Silver": 3,
    "Navy Murano": 3,
    "Acid Wash": 3,
    "Labyrinth": 3,
    "Verdant Growth": 3,
    "Wendigo": 3,
    "Monster Mashup": 3,
    "Spitfire": 3,
    "Black Sand": 3,
    "Shade": 3,
    "Magma": 3,
    "Tatter": 3,
    "Black & Tan": 3,
    "Clay": 3,
    "Slashed": 3,
    "Threat": 3,
    "Tiger Pit": 3,
    "Stained Glass": 3,
    "Stained": 3,
    "Rust Coat": 3,
    "Blue Steel": 3,
    "Night Stripe": 3,
    "DDPAT": 3,
    "Crimson Tsunami": 3,
    "Horizon": 3,
    "Sputnik": 3,
    "Switch Board": 3,
    "Vertigo": 3,
    "Dead Heat": 3,
    "Facility Sketch": 3,
    "Boral": 3,
    "Arctic Wolf": 3,
    "Fissure": 3,
    "Highwayman": 3,
    "Setting Sun": 3,
    " Converter": 3,
    "Enforcer": 3,
    "Eclipse": 3,
    "Violet Murano": 3,
    "Sylvan": 3,
    "Kami": 3,
    "Orange Anolis": 3,
    "Tacticat": 3,
    "Fraud": 3,
    "Amber Slipstream": 3,
    "Watchdog": 3,
    "Cold Fusion": 3,
    "Poly Mag": 3,
    "Black Tie": 3,
    "Pit Viper": 3,
    "Inlay": 3,
    "Memento": 3,
    "Commemoration": 3,
    "Red Stone": 3,
    "Dark Filigree": 3,
    "Deadly Poison": 3,
    "Cerberus": 3,
    "Dynasty": 3,
    "Lionfish": 3,
    "Devourer": 3,
    "Imprint": 3,
    "Warhawk": 3,
    "Mandrel": 3,
    "Cardiac": 3,
    "ScaraB Rush": 3,
    "Souvenir": 3,
    "Paw": 3,
    "Prey": 3,

    # ================================================================
    # Industrial (2) — 工业 / 青色
    # ================================================================
    "Safari Mesh": 2,
    "Anodized Navy": 3,  # 修正: 实际为 Mil-Spec
    "Anodized Steel": 3,  # 修正: 实际为 Mil-Spec
    "Storm": 2,
    "Sage Spray": 2,
    "Contrast Spray": 2,
    "Forest Leaves": 2,
    "Condemned": 2,
    "Hand Brake": 2,
    "Colony": 2,
    "Briar": 2,
    "Groundwater": 2,
    "Scorched": 2,
    "Urban Hazard": 2,
    "Olive Plaid": 2,
    "Birch": 2,
    "Woodsman": 2,
    "Plastique": 2,
    "Steel Disruption": 2,
    "Armor Core": 2,
    "Facility Dark": 2,
    "Surveillance": 2,
    "Flame Test": 2,
    "Coolant": 2,
    "Moon Glow": 2,
    "Reactor": 2,
    "Digital Mesh": 2,
    "Jungle Spray": 2,
    "Tiger Moth": 2,
    "Desert": 2,
    "Gator Mesh": 2,
    "Tropical": 2,
    "Blaze": 2,
    "Rosa": 2,
    "Spray": 2,

    # ================================================================
    # Consumer (1) — 消费 / 白色
    # ================================================================
    "Forest Night": 1,
    "Jungle": 1,
    "Fade Grey": 1,
    "Marbleized": 1,
    "Night": 1,
    "Sand Mesh": 1,
    "Midnight Palm": 1,
    "Gunsmoke": 1,
    "Amber Sheen": 1,
    "Plume": 1,
    "Mudder": 1,
    "Walnut": 1,
    "Death Rattle": 1,
    "Seabird": 1,
    "Winter Forest": 1,
    "Navy Sheen": 1,
    "Tropical Storm": 1,
    "Bone": 1,
    "Waves": 1,
    "Dry Season": 1,
    "Cedar": 1,
    "Picnic": 1,
    "Grassland": 1,
    "Dust": 1,
    "Moss": 1,
    "Fallout": 1,
    "Leaves": 1,
    "Numbers": 1,
    "Tiger": 1,
    "Bulb": 1,
    "Tape": 1,
    "Spider Lily": 1,
    "Splash": 1,
    "Mayan": 1,
    "Magenta": 1,
    "Madurai": 1,
    "Radiation": 1,
    "Oil Change": 1,
    "Slide": 1,
    "Reinforced": 1,
    "War": 1,
    "Hive": 1,
    "Jungle Slipstream": 1,
    "Hazard": 1,
    "Carbon Fiber": 1,
    "Snake": 1,
    "Tread Plate": 1,
    "Fizzy": 1,
    "Gila": 1,
    "Dusk": 1,
    "Sand": 1,
    "Huntsman": 1,
    "Mist": 1,
    "Shrub": 1,
    "Marsh": 1,
    "Grain": 1,

    # ================================================================
    # Contraband (8) — 违禁品 / 橙色
    # ================================================================
    "Howl": 8,

    # ================================================================
    # 补充: 最终训练集中出现的皮肤 (2026-07-15 数据准备)
    # 这些是 Top 150 高流动性物品中原本缺失稀有度的皮肤
    # ================================================================

    # --- Covert (6) ---
    "Sun in Leo": 6,
    "Wildfire": 6,
    "Nightmare": 6,
    "Player Two": 6,
    "The Kraken": 6,  # Sawed-Off | The Kraken, Winter Offensive Case

    # --- Classified (5) ---
    "Fowl Play": 5,
    "Carnivore": 5,
    "See Ya Later": 5,
    "Remote Control": 5,
    "Quicksilver": 5,

    # --- Restricted (4) ---
    "Slate": 4,
    "Midnight Lily": 4,
    "Circaetus": 4,
    "Light Rail": 4,
    "Decommissioned": 4,
    "Retrobution": 4,
    "Connexion": 4,
    "Pipe Down": 4,
    "Stalker": 4,
    "SWAG-7": 4,
    "Kitbash": 4,
    "Ruby Poison Dart": 4,
    "Starlight Protector": 4,
    "Fallout Warning": 4,
    "Aloha": 4,
    "Hades": 4,
    "Wild Child": 4,
    "Mount Fuji": 4,  # MP9 | Mount Fuji, Operation Riptide Case
    "Osiris": 4,  # PP-Bizon | Osiris, Operation Breakout Case

    # --- Mil-Spec (3) ---
    "Uncharted": 3,
    "Framework": 3,
    "Blue Ply": 3,
    "Copper Galaxy": 3,
    "Sandstorm": 3,
    "Dragon Tattoo": 3,
    "Ironwork": 3,
    "Mainframe": 3,
    "Modern Hunter": 3,
    "Palm": 3,
    "Music Box": 3,
    "Quick Sand": 3,
    "Sobek's Bite": 3,
    "Mint Kimono": 3,
    "Leather": 3,
    "Run and Hide": 3,
    "Damascus Steel": 3,
    "Danger Close": 3,
    "Corporal": 3,
    "Oscillator": 3,

    # --- Industrial (2) ---
    "Torque": 2,
    "CaliCamo": 2,
    "Cyanospatter": 2,
    "Blue Fissure": 2,
    "Magnesium": 2,
    "Contaminant": 2,
    "Canal Spray": 2,

    # --- Consumer (1) ---
    "Contractor": 1,
}

# ============================================================
# 皮肤 finish 名称标准化 (处理同一皮肤的不同写法)
# ============================================================
_ALIASES = {
    # 拼写修正 / 同义词映射 (key 和 target 都是 skin finish 名称, 不含武器前缀)
    "Lighting Strike": "Lightning Strike",  # 常见拼写错误
    "Hyperbeast": "Hyper Beast",  # 常见连写
}

# 将别名添加为额外的 RARITY_MAP 条目
for _alias, _target in _ALIASES.items():
    if _target in RARITY_MAP:
        RARITY_MAP[_alias] = RARITY_MAP[_target]

# ============================================================
# 武器基础类型 → 大类别映射
# ============================================================
WEAPON_CATEGORY: dict[str, str] = {
    "AK-47": "Rifle",
    "M4A1-S": "Rifle",
    "M4A4": "Rifle",
    "AWP": "Rifle",
    "FAMAS": "Rifle",
    "Galil AR": "Rifle",
    "AUG": "Rifle",
    "SG 553": "Rifle",
    "SSG 08": "Rifle",
    "SCAR-20": "Rifle",
    "G3SG1": "Rifle",

    "USP-S": "Pistol",
    "Glock-18": "Pistol",
    "Desert Eagle": "Pistol",
    "P250": "Pistol",
    "Five-SeveN": "Pistol",
    "Tec-9": "Pistol",
    "CZ75-Auto": "Pistol",
    "R8 Revolver": "Pistol",
    "Dual Berettas": "Pistol",
    "P2000": "Pistol",

    "MP9": "SMG",
    "MAC-10": "SMG",
    "UMP-45": "SMG",
    "P90": "SMG",
    "MP7": "SMG",
    "MP5-SD": "SMG",
    "PP-Bizon": "SMG",

    "XM1014": "Heavy",
    "MAG-7": "Heavy",
    "Nova": "Heavy",
    "Sawed-Off": "Heavy",
    "M249": "Heavy",
    "Negev": "Heavy",

    "Butterfly Knife": "Knife",
    "Karambit": "Knife",
    "M9 Bayonet": "Knife",
    "Bayonet": "Knife",
    "Talon Knife": "Knife",
    "Skeleton Knife": "Knife",
    "Flip Knife": "Knife",
    "Bowie Knife": "Knife",
    "Huntsman Knife": "Knife",
    "Falchion Knife": "Knife",
    "Shadow Daggers": "Knife",
    "Gut Knife": "Knife",
    "Navaja Knife": "Knife",
    "Stiletto Knife": "Knife",
    "Ursus Knife": "Knife",
    "Classic Knife": "Knife",
    "Paracord Knife": "Knife",
    "Survival Knife": "Knife",
    "Nomad Knife": "Knife",

    "Sport Gloves": "Glove",
    "Specialist Gloves": "Glove",
    "Driver Gloves": "Glove",
    "Hand Wraps": "Glove",
    "Moto Gloves": "Glove",
    "Bloodhound Gloves": "Glove",
    "Hydra Gloves": "Glove",

    "Danger Zone Case": "Case",
    "Prisma Case": "Case",
    "Clutch Case": "Case",
    "Chroma Case": "Case",
    "Spectrum Case": "Case",
    "Fracture Case": "Case",
    "Recoil Case": "Case",
    "Revolution Case": "Case",
    "Dreams & Nightmares Case": "Case",
    "CS:GO Weapon Case": "Case",
    "Operation Bravo Case": "Case",
    "Operation Phoenix Case": "Case",
    "Operation Breakout Case": "Case",
    "Operation Vanguard Case": "Case",
    "Operation Wildfire Case": "Case",
    "Operation Hydra Case": "Case",
    "Shadow Case": "Case",
    "Falchion Case": "Case",
    "Revolver Case": "Case",
    "Gamma Case": "Case",
    "Gamma 2 Case": "Case",
    "Glove Case": "Case",
    "Horizon Case": "Case",
}


def parse_item_name(
    market_hash_name: str,
) -> dict:
    """
    解析 market_hash_name 提取结构化元数据。

    示例:
      "AK-47 | Redline (Field-Tested)" →
        weapon_type="AK-47", skin="Redline", wear="FT", is_stattrak=False
      "StatTrak™ AK-47 | Redline (Factory New)" →
        weapon_type="AK-47", skin="Redline", wear="FN", is_stattrak=True
      "★ Butterfly Knife | Fade (Minimal Wear)" →
        weapon_type="Butterfly Knife", skin="Fade", wear="MW", is_stattrak=False
    """
    name = market_hash_name

    # 1. 检测 StatTrak
    is_stattrak = "StatTrak" in name
    # 移除 StatTrak 前缀
    if is_stattrak:
        name = name.replace("StatTrak™ ", "").replace("StatTrak ", "")

    # 2. 检测 Souvenir
    is_souvenir = "Souvenir" in name
    if is_souvenir:
        name = name.replace("Souvenir ", "")

    # 3. 解析 wear condition (括号中的部分)
    wear_map = {
        "(Factory New)": "FN",
        "(Minimal Wear)": "MW",
        "(Field-Tested)": "FT",
        "(Well-Worn)": "WW",
        "(Battle-Scarred)": "BS",
    }
    wear = None
    for full_wear, short_wear in wear_map.items():
        if full_wear in name:
            wear = short_wear
            name = name.replace(f" {full_wear}", "").replace(full_wear, "")
            break

    # 4. 检测 ★ 标记 (刀/手套)
    is_rare = name.startswith("★ ")
    name = name.replace("★ ", "")

    # 5. 分离 weapon_type 和 skin finish
    if " | " in name:
        weapon_type, skin = name.split(" | ", 1)
    else:
        # 箱子/胶囊等没有 | 分隔符
        weapon_type = name
        skin = ""

    weapon_type = weapon_type.strip()
    skin = skin.strip()

    # 6. 确定类别
    category = WEAPON_CATEGORY.get(weapon_type, None)

    # 7. 确定稀有度
    # 先查 skin finish 映射
    rarity = RARITY_MAP.get(skin, None)
    # 刀和手套默认 Rare (Gold)
    # 刀/手套始终是 Rare (金), 无论 skin finish 名称是否在 RARITY_MAP 中
    if category in ("Knife", "Glove"):
        rarity = 7
    # 箱子 — 用 0 表示基础等级
    if category == "Case":
        rarity = 0

    return {
        "weapon_type": weapon_type,
        "skin": skin,
        "wear": wear,
        "is_stattrak": int(is_stattrak),
        "is_souvenir": int(is_souvenir),
        "category": category,
        "rarity_level": rarity,
    }


def get_rarity_name(level: int | None, category: str | None = None) -> str | None:
    """将稀有度数值转换为名称"""
    names = {
        0: "Base Grade",       # 箱子等基础物品
        1: "Consumer Grade",
        2: "Industrial Grade",
        3: "Mil-Spec",
        4: "Restricted",
        5: "Classified",
        6: "Covert",
        7: "Rare",
        8: "Contraband",
    }
    if level is not None:
        return names.get(level, "Unknown")
    return None  # 真正未知的稀有度
