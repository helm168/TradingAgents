"""Scoring module: extract technical/fundamental scores from analyst reports.

四个量化打分模块 (跟 LLM 主观分析独立):
  fscore.py  Piotroski F-Score 0-9  (改善 trend, 距离破产线)
  qscore.py  Quality 0-100          (绝对盈利能力 + 财务健康)
  gscore.py  Growth 0-100           (营收/净利 CAGR + YoY)
  vscore.py  Value 0-100            (PE/PB/PEG)

四者独立, 业界 Quality / Growth / Value 是独立因子 (Fama-French/MSCI 都这么分).
"""
from .score_extractor import score_reports, ScoreResult
from .fscore import compute_fscore, FScoreResult
from .qscore import compute_qscore, QScoreResult
from .gscore import compute_gscore, GScoreResult
from .vscore import compute_vscore, VScoreResult

__all__ = [
    "score_reports", "ScoreResult",
    "compute_fscore", "FScoreResult",
    "compute_qscore", "QScoreResult",
    "compute_gscore", "GScoreResult",
    "compute_vscore", "VScoreResult",
]
