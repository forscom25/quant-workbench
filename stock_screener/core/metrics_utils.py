import numpy as np
import pandas as pd
from typing import List, Tuple

from core.schema import MetricStatus

def compute_std(series: List[float], min_valid_points: int = 4) -> Tuple[float, str]:
    """
    시계열 데이터의 표본표준편차를 계산합니다.
    - series: 분기별 단독 영업이익률 등의 시계열 리스트 (최신 -> 과거 순 등 무관)
    - min_valid_points: 통계적 유의성을 갖추기 위한 최소 유효 데이터 개수
    
    Returns:
        (계산된 표준편차, MetricStatus)
    """
    # NaN이 아닌 유효한 데이터만 추출
    valid = [x for x in series if not pd.isna(x)]
    
    if len(valid) < min_valid_points:
        return float('nan'), MetricStatus.NOT_COMPUTABLE
    
    # 표본표준편차 (ddof=1 적용)
    std_val = float(np.std(valid, ddof=1))
    
    return std_val, MetricStatus.COMPUTED

def calc_zscore(series: pd.Series) -> pd.Series:
    """극단값(1%) 클리핑 후 Z-score 반환"""
    clipped = series.clip(lower=series.quantile(0.01), upper=series.quantile(0.99))
    if clipped.std() == 0:
        return pd.Series(0, index=series.index)
    return (clipped - clipped.mean()) / clipped.std()

def compute_composite_score(df: pd.DataFrame, score_mapping: dict) -> pd.Series:
    """
    score_mapping = {'z_score_column_name': weight_float, ...}
    지정된 Z-score 컬럼과 가중치를 곱하여 합산 점수 반환
    """
    score = pd.Series(0.0, index=df.index)
    for col, weight in score_mapping.items():
        score += df[col] * weight
    return score

def apply_percentile_filter(df: pd.DataFrame, score_col: str, top_percentile: float) -> tuple[pd.DataFrame, float]:
    """합산 점수 기준 상위 N% 필터링 (결측치 제외 후 임계치 계산)"""
    valid_scores = df[score_col].dropna()
    if valid_scores.empty:
        return df.iloc[0:0], 0.0 # 빈 데이터프레임 반환
        
    threshold = valid_scores.quantile(1 - top_percentile)
    
    # 점수를 통과했거나, 애초에 NOT_COMPUTABLE로 구제(NaN)된 종목 포함
    mask = (df[score_col] >= threshold) | df[score_col].isna()
    return df[mask].copy(), threshold