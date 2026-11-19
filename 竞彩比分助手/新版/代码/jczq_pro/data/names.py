"""中英文队名对齐。未收录的队名保持原名，Elo/xG 会自然降级。"""
CN_TO_EN = {
    # 西甲
    "巴塞罗那": "Barcelona", "皇家马德里": "Real Madrid", "马德里竞技": "Ath Madrid",
    "塞维利亚": "Sevilla", "比利亚雷亚尔": "Villarreal", "皇家贝蒂斯": "Betis",
    "皇家社会": "Sociedad", "瓦伦西亚": "Valencia", "毕尔巴鄂竞技": "Ath Bilbao",
    "赫罗纳": "Girona", "塞尔塔": "Celta", "西班牙人": "Espanyol",
    "巴列卡诺": "Vallecano", "奥萨苏纳": "Osasuna", "马略卡": "Mallorca",
    "阿拉维斯": "Alaves", "赫塔费": "Getafe", "莱万特": "Levante",
    "埃尔切": "Elche", "奥维耶多": "Oviedo",
    # 德甲
    "拜仁慕尼黑": "Bayern Munich", "多特蒙德": "Dortmund", "勒沃库森": "Leverkusen",
    "莱比锡红牛": "RB Leipzig", "斯图加特": "Stuttgart", "法兰克福": "Ein Frankfurt",
    "沃尔夫斯堡": "Wolfsburg", "门兴格拉德巴赫": "M'gladbach", "云达不莱梅": "Werder Bremen",
    "弗赖堡": "Freiburg", "霍芬海姆": "Hoffenheim", "美因茨": "Mainz",
    "奥格斯堡": "Augsburg", "柏林联合": "Union Berlin", "汉堡": "Hamburg",
    "科隆": "FC Koln", "海登海姆": "Heidenheim", "圣保利": "St Pauli",
    # 意甲
    "国际米兰": "Inter", "AC米兰": "Milan", "尤文图斯": "Juventus",
    "那不勒斯": "Napoli", "罗马": "Roma", "拉齐奥": "Lazio",
    "亚特兰大": "Atalanta", "佛罗伦萨": "Fiorentina", "博洛尼亚": "Bologna",
    "都灵": "Torino", "乌迪内斯": "Udinese", "热那亚": "Genoa",
    "卡利亚里": "Cagliari", "莱切": "Lecce", "维罗纳": "Verona",
    "帕尔马": "Parma", "科莫": "Como", "萨索洛": "Sassuolo",
    "比萨": "Pisa", "克雷莫纳": "Cremonese",
    # 法甲
    "巴黎圣日尔曼": "Paris SG", "马赛": "Marseille", "摩纳哥": "Monaco",
    "里尔": "Lille", "里昂": "Lyon", "尼斯": "Nice", "朗斯": "Lens",
    "雷恩": "Rennes", "斯特拉斯堡": "Strasbourg", "南特": "Nantes",
    "图卢兹": "Toulouse", "欧塞尔": "Auxerre", "布雷斯特": "Brest",
    "昂热": "Angers", "勒阿弗尔": "Le Havre", "洛里昂": "Lorient",
    "梅斯": "Metz", "巴黎FC": "Paris FC",
    # 荷甲
    "费耶诺德": "Feyenoord", "阿贾克斯": "Ajax", "埃因霍温": "PSV",
    "阿尔克马尔": "AZ Alkmaar", "特温特": "Twente", "乌得勒支": "Utrecht",
    # 土超 / 葡超 / 比甲
    "加拉塔萨雷": "Galatasaray", "贝西克塔斯": "Besiktas", "费内巴切": "Fenerbahce",
    "特拉布宗": "Trabzonspor", "本菲卡": "Benfica", "波尔图": "Porto",
    "葡萄牙体育": "Sporting", "布拉加": "Braga", "布鲁日": "Club Brugge",
    "安德莱赫特": "Anderlecht",
    # 英超
    "曼彻斯特联": "Man United", "曼彻斯特城": "Man City", "利物浦": "Liverpool",
    "阿森纳": "Arsenal", "切尔西": "Chelsea", "热刺": "Tottenham",
    "纽卡斯尔联": "Newcastle", "阿斯顿维拉": "Aston Villa", "布莱顿": "Brighton",
    "西汉姆联": "West Ham", "埃弗顿": "Everton", "富勒姆": "Fulham",
    "水晶宫": "Crystal Palace", "布伦特福德": "Brentford", "狼队": "Wolves",
    "伯恩茅斯": "Bournemouth", "诺丁汉森林": "Nott'm Forest", "利兹联": "Leeds",
    "伯恩利": "Burnley", "桑德兰": "Sunderland",
    # 苏超
    "凯尔特人": "Celtic", "格拉斯哥流浪者": "Rangers",
}

EN_TO_CN = {v: k for k, v in CN_TO_EN.items()}


def canon(team: str) -> str:
    return CN_TO_EN.get(team, team)


def display_cn(team: str) -> str:
    return EN_TO_CN.get(team, team)