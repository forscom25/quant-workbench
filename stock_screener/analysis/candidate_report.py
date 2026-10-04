import pandas as pd
from typing import Dict, List, Optional

from analysis.screening_stats import STAGE_ORDER, build_rejection_report

# 후보 순위에 쓰는 단계별 합산 점수. 단계마다 스케일이 비슷한 Z-score 기반이라 단순 평균으로 묶는다.
# (가중치를 세분화하지 않는 이유: 공통 설계 원칙 9번 — 정교화는 기본적으로 의심한다)
SCORE_COLS = ['stage3_score', 'stage4_score', 'stage5_score']

# 리포트에 보여줄 핵심 지표. history에 없는 컬럼은 건너뛴다(실행 시점/설정에 따라 달라질 수 있음).
DETAIL_COLS = ['name', 'sector', 'market_cap', 'roe', 'roic', 'op_margin_std',
               'sales_growth_yoy', 'pbr', 'per', 'psr', 'bps_growth',
               'debt_ratio', 'interest_coverage_ratio']
FLAG_COLS = ['is_cost_cutting_warning', 'is_pbr_value_trap', 'warning_tags', 'na_reasons']


def _existing(df: pd.DataFrame, cols: List[str]) -> List[str]:
    return [c for c in cols if c in df.columns]


DEFAULT_MIN_MARKET_CAP = 1e11  # 1,000억 원 (2026-10-04 사용자 지정)


def _exclusion_reasons(df: pd.DataFrame, min_market_cap: Optional[float], exclude_loss: bool,
                       min_icr: Optional[float]) -> pd.Series:
    """shortlist 제외 사유를 행마다 문자열로 반환한다(빈 문자열이면 제외 안 됨).
    결측값은 "판단 불가"이므로 제외하지 않는다 — 데이터가 없다는 이유로 조용히 빼면 근거가 안 보임."""
    checks = []  # (조건 마스크, 사유 라벨)
    if min_market_cap is not None and 'market_cap' in df.columns:
        checks.append((df['market_cap'] < min_market_cap, f'시총<{min_market_cap / 1e8:,.0f}억'))
    if exclude_loss and 'roe' in df.columns:
        checks.append((df['roe'] < 0, 'ROE<0'))
    if min_icr is not None and 'interest_coverage_ratio' in df.columns:
        checks.append((df['interest_coverage_ratio'] < min_icr, f'ICR<{min_icr:g}'))

    return pd.Series(
        [','.join(label for mask, label in checks if mask.loc[idx]) for idx in df.index],
        index=df.index,
    )


def build_candidate_report(history: Dict[str, pd.DataFrame], max_per_sector: int = 1, top_n: int = 5,
                           min_market_cap: Optional[float] = DEFAULT_MIN_MARKET_CAP,
                           exclude_loss: bool = False, min_icr: Optional[float] = None) -> pd.DataFrame:
    """
    최종 통과 종목을 점수순으로 정렬하고, 섹터 쏠림을 막는 shortlist 여부를 표시한다 (순수 함수).

    5종목 안팎의 집중 포트폴리오에서는 같은 섹터 종목이 몰리는 것이 가장 흔한 약점이라,
    점수 상위부터 섹터당 max_per_sector개까지만 shortlist=True로 표시한다. 통과 종목 전체는
    shortlist 여부와 무관하게 그대로 남겨 사람이 직접 비교할 수 있게 한다.

    min_market_cap/exclude_loss/min_icr은 파이프라인 판정이 아니라 "집중 포트의 유동성·위험 제약"으로,
    shortlist에서만 빼고 excluded_reason에 사유를 남긴다(예측력 근거가 있는 필터가 아님).
    """
    final_df = history.get('stage5')
    if final_df is None or final_df.empty:
        return pd.DataFrame()

    passed = final_df[final_df['fail_reason'].isnull()].copy() if 'fail_reason' in final_df.columns else final_df.copy()
    if passed.empty:
        return pd.DataFrame()

    score_cols = _existing(passed, SCORE_COLS)
    # 점수가 결측(구제 통과 등)인 단계는 평균에서 빼되, 전부 결측이면 최하위로 보낸다.
    passed['rank_score'] = passed[score_cols].mean(axis=1, skipna=True) if score_cols else float('nan')
    passed = passed.sort_values('rank_score', ascending=False, na_position='last').reset_index(drop=True)

    passed['excluded_reason'] = _exclusion_reasons(passed, min_market_cap, exclude_loss, min_icr)
    # Stage3 점수가 비어 있으면 구제(exempt) 통과라 Stage3 평가 없이 순위가 매겨진 것 — 눈에 띄게 표시한다.
    passed['stage3_unscored'] = passed['stage3_score'].isna() if 'stage3_score' in passed.columns else False

    sector_count: Dict[str, int] = {}
    picks = []
    sectors = passed['sector'] if 'sector' in passed.columns else [None] * len(passed)
    for sector, excluded in zip(sectors, passed['excluded_reason']):
        used = sector_count.get(sector, 0)
        pick = (not excluded) and used < max_per_sector and sum(picks) < top_n
        if pick:
            sector_count[sector] = used + 1
        picks.append(pick)
    passed['shortlist'] = picks

    cols = (['ticker'] + _existing(passed, DETAIL_COLS) + score_cols + ['rank_score', 'stage3_unscored']
            + _existing(passed, FLAG_COLS) + ['excluded_reason', 'shortlist'])
    return passed[cols]


def build_holdings_check(history: Dict[str, pd.DataFrame], holdings: Dict[str, str]) -> pd.DataFrame:
    """
    보유 종목(ticker -> 이름)이 이번 스크리닝에서 어느 단계까지 갔고 왜 탈락/통과했는지 정리한다 (순수 함수).

    탈락 판정은 build_rejection_report를 그대로 재사용한다(같은 로직을 두 곳에 두면 따로 낡음).
    Stage1은 섹터 단위 판정이라 SECTOR_NOT_QUALIFIED면 개별 기업 펀더멘털은 평가되지 않았다는 점을
    note로 함께 남긴다 — "섹터가 소외되지 않아서 탈락"과 "기업이 나빠서 탈락"을 구분해서 읽어야 한다.
    """
    report = build_rejection_report(history).set_index('ticker') if history else pd.DataFrame()

    # Stage1 섹터 점수/순위: 섹터에서 탈락한 보유 종목이 "기준에서 얼마나 멀었는지"를 읽는 용도.
    # composite_score가 높을수록 소외(통과 쪽)이므로 내림차순 순위를 매긴다(Stage1 판정과 동일 방향).
    sector_info = pd.DataFrame()
    s1 = history.get('stage1') if history else None
    if s1 is not None and {'sector', 'composite_score'}.issubset(s1.columns):
        sector_info = s1.drop_duplicates('sector').set_index('sector')
        sector_info['sector_rank'] = sector_info['composite_score'].rank(ascending=False, method='min')
        n_sectors = len(sector_info)
    rows = []
    for ticker, name in holdings.items():
        row = {'ticker': ticker, 'holding_name': name}
        if report.empty or ticker not in report.index:
            row.update({'status': 'NOT_IN_UNIVERSE', 'last_stage': '', 'fail_reason': '', 'na_reasons': '',
                        'note': '유니버스에 없음(코스피 외, 상장폐지, 티커 오류 등)'})
            rows.append(row)
            continue

        r = report.loc[ticker]
        row.update({'status': r['status'], 'last_stage': r['last_stage'],
                    'fail_reason': r['fail_reason'], 'na_reasons': r['na_reasons']})
        row['note'] = '섹터가 Stage1에서 탈락해 개별 기업은 평가되지 않음' if r['fail_reason'] == 'sector_not_qualified' else ''

        # 도달한 마지막 단계의 핵심 지표/점수를 붙여, 탈락이어도 "어느 정도로 떨어졌는지" 보이게 한다.
        last_df = history.get(r['last_stage'])
        if last_df is not None:
            detail = last_df[last_df['ticker'] == ticker]
            if not detail.empty:
                for col in _existing(detail, ['sector'] + DETAIL_COLS[2:] + SCORE_COLS):
                    row[col] = detail.iloc[0][col]

        sector = row.get('sector')
        if not sector_info.empty and sector in sector_info.index:
            si = sector_info.loc[sector]
            row['sector_rank'] = f"{int(si['sector_rank'])}/{n_sectors}"
            for col in _existing(sector_info, ['return_z_score', 'volume_z_score', 'composite_score']):
                row[f'sector_{col}'] = si[col]
        rows.append(row)

    return pd.DataFrame(rows)


def build_stage_snapshot(history: Dict[str, pd.DataFrame], stage: str, tickers: Optional[List[str]] = None) -> pd.DataFrame:
    """특정 단계 history에서 지정 종목들의 row를 그대로 꺼낸다(상세 비교용, 순수 함수)."""
    if stage not in STAGE_ORDER:
        raise ValueError(f"알 수 없는 stage: {stage}")
    df = history.get(stage)
    if df is None or df.empty:
        return pd.DataFrame()
    return df if tickers is None else df[df['ticker'].isin(tickers)]
