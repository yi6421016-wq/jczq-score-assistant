"""运行配置。旧版数据只读，新版产物单独落盘，避免污染封存样本。"""
import os
from pathlib import Path

HOME = Path(os.environ.get("JCZQ_HOME", str(Path.home() / "竞彩比分助手")))
DATA_DIR = HOME / "数据"
BACKFILL_DIR = DATA_DIR / "回溯"
REPORT_DIR = HOME / "报告"
# 新版自己的产物，不覆盖旧版
PRO_DIR = Path(os.environ.get("JCZQ_PRO_HOME", str(HOME / "新版")))
PRO_DATA = PRO_DIR / "数据"
PRO_REPORT = PRO_DIR / "报告"
EXTERNAL_DIR = PRO_DIR / "外部数据"

SOURCE = "https://trade.500.com/jczq/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MAX_GOALS = 10
DC_RHO = -0.10              # 旧版 396 场校准值，作为先验
HF_SHARE = 0.45
HF_VALUES = ["3-3", "3-1", "3-0", "1-3", "1-1", "1-0", "0-3", "0-1", "0-0"]
HF_TXT = {"3": "胜", "1": "平", "0": "负"}

# 集成先验权重；缺覆盖时按可用模型重新归一化
ENSEMBLE_PRIOR = {"dc": 0.40, "elo": 0.25, "xg": 0.15, "ml": 0.20}
# 市场比分盘在“比分分布”里的权重（不影响 1X2 集成）
# 0.15 为同窗口回测里 Top1/Top3/Top5 均不退步的上限附近
MARKET_SCORE_SHARE = 0.15

# Elo
ELO_INIT = 1500.0
ELO_HOME = 60.0
ELO_K = 20.0
DRAW_BASE = 0.27
DRAW_SCALE = 320.0

# ML
ML_MIN_TRAIN = 300
ML_RETRAIN_EVERY = 200
ML_MAX_ITER = 140
ML_LEARNING_RATE = 0.06
ML_MAX_DEPTH = 3
ML_L2 = 1.0
ML_MIN_LEAF = 20

# xG
XG_MIN_MATCHES = 3
XG_DECAY = 0.80


def ensure_dirs():
    for p in (PRO_DIR, PRO_DATA, PRO_REPORT, EXTERNAL_DIR):
        p.mkdir(parents=True, exist_ok=True)