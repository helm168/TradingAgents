"""Scoring module: extract technical/fundamental scores from analyst reports."""
from .score_extractor import score_reports, ScoreResult

__all__ = ["score_reports", "ScoreResult"]
