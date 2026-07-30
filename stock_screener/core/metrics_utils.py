import numpy as np
import pandas as pd
from typing import List, Tuple

# 상태 태깅을 위한 간단한 Enum 클래스 (schema.py에 있다면 import 해서 사용)
class MetricStatus:
    COMPUTED = "COMPUTED"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"
    CAUTION = "CAUTION"

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