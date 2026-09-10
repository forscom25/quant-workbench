import pandas as pd
from typing import Dict

# pipeline.py가 항상 이 순서/이름으로 history dict를 채움 (core/pipeline.py 참고)
STAGE_ORDER = ['stage1', 'stage2', 'stage3', 'stage4', 'stage5']

def build_funnel_summary(history: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    각 단계에 도달한 종목 수와 그중 통과한 수를 집계합니다 (순수 함수).
    stage 하나가 비어있으면(그 이전 단계에서 전멸) 이후 stage는 아예 history에 없을 수 있음 —
    이 경우 total=0, passed=0으로 채워 깔때기가 어디서 끊겼는지 그대로 드러낸다.
    """
    rows = []
    for stage_name in STAGE_ORDER:
        df = history.get(stage_name)
        if df is None or df.empty:
            rows.append({'stage': stage_name, 'total': 0, 'passed': 0})
            continue
        total = len(df)
        passed = int(df['fail_reason'].isnull().sum()) if 'fail_reason' in df.columns else total
        rows.append({'stage': stage_name, 'total': total, 'passed': passed})
    return pd.DataFrame(rows)

def build_rejection_report(history: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    티커별로 "어느 단계까지 도달했고, 거기서 왜 탈락(혹은 최종 통과)했는지"를 한 줄로 정리합니다.

    각 history[stageN]에는 그 단계를 실제로 평가받은 종목만 들어있다(직전 단계 통과자만 다음
    단계로 넘어가므로) — 즉 어떤 티커가 "마지막으로 등장하는" stage가 곧 그 티커가 도달한
    최종 단계다. 그 단계에서 fail_reason이 비어있고, 그 단계가 실제로 파이프라인이 도달한
    마지막 단계와 같다면 최종 통과(PASSED)로, 아니면 그 단계의 fail_reason이 탈락 사유다.
    """
    last_seen = {}  # ticker -> (stage_name, fail_reason, na_reasons)
    max_stage_reached = None

    for stage_name in STAGE_ORDER:
        df = history.get(stage_name)
        if df is None or df.empty:
            continue
        max_stage_reached = stage_name

        na_series = df['na_reasons'] if 'na_reasons' in df.columns else pd.Series([''] * len(df), index=df.index)
        fail_series = df['fail_reason'] if 'fail_reason' in df.columns else pd.Series([None] * len(df), index=df.index)

        for ticker, fail_reason, na_reason in zip(df['ticker'], fail_series, na_series):
            last_seen[ticker] = (stage_name, fail_reason, na_reason)

    rows = []
    for ticker, (stage_name, fail_reason, na_reason) in last_seen.items():
        passed_final = pd.isnull(fail_reason) and stage_name == max_stage_reached
        rows.append({
            'ticker': ticker,
            'last_stage': stage_name,
            'status': 'PASSED' if passed_final else 'REJECTED',
            'fail_reason': '' if pd.isnull(fail_reason) else fail_reason,
            'na_reasons': '' if pd.isnull(na_reason) else na_reason,
        })

    if not rows:
        return pd.DataFrame(columns=['ticker', 'last_stage', 'status', 'fail_reason', 'na_reasons'])

    result = pd.DataFrame(rows)
    return result.sort_values(['status', 'last_stage', 'ticker']).reset_index(drop=True)
