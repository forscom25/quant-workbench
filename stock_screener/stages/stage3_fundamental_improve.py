import pandas as pd
import numpy as np
import logging
from core.metrics_utils import calc_zscore, compute_composite_score, apply_percentile_filter

from core.schema import (
    TurnaroundCols, 
    TurnaroundMetrics, 
    FailReason, 
    MetricStatus, 
    validate_schema
)

class FundamentalImproveScreener:
    """
    3단계: 체질 개선 (Turnaround)
    판관비율 감소, 재고회전율 상승, GPM(매출총이익률) 개선 등 
    실제 펀더멘털이 개선되고 있는지(가짜 턴어라운드가 아닌지) 검증합니다.
    """
    def __init__(self, params: dict):
        self.lookback_q = params.get('sga_lookback_quarters', 6)

        # [수정] YAML에서 받아올 가중치 및 통과 기준
        self.sales_weight = params.get('sales_weight', 0.4)
        self.sga_weight = params.get('sga_weight', 0.4)
        self.gpm_weight = params.get('gpm_weight', 0.2)
        self.pass_percentile = params.get('composite_pass_percentile', 0.3)
        self.sales_decline_threshold = params.get('cost_cutting_only_sales_decline_threshold', 0.00)
        
        self.logger = logging.getLogger(__name__)

    def run(self, sector_tickers_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        metrics_data = []

        for _, row in sector_tickers_df.iterrows():
            ticker = row['ticker']
            sector = row['sector']
            
            # 1. 시계열 원자료 조달
            q_series = loader.get_quarterly_financials_series(ticker, base_date, n_quarters=self.lookback_q)
            
            na_reasons = []
            
            # 🔴 버그 2 수정: q_series[0]만 반복 검사하던 논리 오류 해결
            if len(q_series) < 6 or all(pd.isna(q.get('revenue', np.nan)) for q in q_series[:6]):
                na_reasons['DATA_TOO_SHORT'] = (MetricStatus.NOT_COMPUTABLE, "최근 6분기 재무 데이터 부족")
                metrics_data.append({
                    'ticker': ticker, 
                    'sector': sector,
                    TurnaroundCols.sga_yoy_avg: np.nan, 
                    TurnaroundCols.sales_growth_yoy: np.nan, 
                    TurnaroundCols.gpm_yoy: np.nan,
                    TurnaroundCols.stage3_score: np.nan,
                    TurnaroundCols.is_cost_cutting_warning: False,
                    TurnaroundCols.inventory_turnover_yoy: np.nan, # 스키마 요구사항(Optional)
                    TurnaroundCols.na_reasons: na_reasons
                })
                continue

            def safe_div(n, d):
                return np.divide(n, d) if pd.notna(n) and pd.notna(d) and d != 0 else np.nan

            # --- 판관비액 (SGA) ---
            # [수정] 단순 비율(Ratio) 감소 연속성이 아닌, 2개 분기 판관비 '증감률'의 평균 사용
            sga_t0 = q_series[0].get('sga')
            sga_t4 = q_series[4].get('sga')
            sga_t1 = q_series[1].get('sga')
            sga_t5 = q_series[5].get('sga')
            
            sga_yoy_q1 = safe_div(sga_t0 - sga_t4, abs(sga_t4)) if pd.notna(sga_t4) else np.nan
            sga_yoy_q2 = safe_div(sga_t1 - sga_t5, abs(sga_t5)) if pd.notna(sga_t5) else np.nan
            sga_yoy_avg = np.nanmean([sga_yoy_q1, sga_yoy_q2]) if pd.notna(sga_yoy_q1) or pd.notna(sga_yoy_q2) else np.nan

            # --- 매출액 성장률 (Sales Growth YoY) ---
            rev_t0 = q_series[0].get('revenue', np.nan)
            rev_t4 = q_series[4].get('revenue', np.nan)
            sales_growth_yoy = safe_div(rev_t0 - rev_t4, abs(rev_t4))

            # --- 매출총이익률 (GPM) ---
            gp_t0 = q_series[0].get('gross_profit', np.nan)
            if pd.isna(gp_t0):
                na_reasons['TURNAROUND_NOT_COMPUTABLE'] = (MetricStatus.NOT_COMPUTABLE, "GPM 계산 불가")
            
            gpm_t0 = safe_div(gp_t0, rev_t0)
            gpm_t4 = safe_div(q_series[4].get('gross_profit', np.nan), rev_t4)
            gpm_yoy = gpm_t0 - gpm_t4

            # 경고 태그 (매출 역성장)
            is_cost_cutting_warning = bool(pd.notna(sales_growth_yoy) and sales_growth_yoy <= self.sales_decline_threshold)

            metrics_data.append({
                'ticker': ticker, 
                'sector': sector,
                TurnaroundCols.sga_yoy_avg: sga_yoy_avg,
                TurnaroundCols.sales_growth_yoy: sales_growth_yoy,
                TurnaroundCols.gpm_yoy: gpm_yoy,
                TurnaroundCols.stage3_score: np.nan,
                TurnaroundCols.is_cost_cutting_warning: is_cost_cutting_warning,
                TurnaroundCols.inventory_turnover_yoy: np.nan,
                TurnaroundCols.na_reasons: na_reasons
            })

        df = pd.DataFrame(metrics_data)
        if df.empty: return df

        # ---------------------------------------------------------
        # 3. Composite Score 스코어링 및 필터링
        # ---------------------------------------------------------
        df['sales_z'] = calc_zscore(df[TurnaroundCols.sales_growth_yoy])
        df['sga_inv_z'] = calc_zscore(-df[TurnaroundCols.sga_yoy_avg]) # 판관비 증가는 나쁘므로 부호 반전
        df['gpm_z'] = calc_zscore(df[TurnaroundCols.gpm_yoy])
        
        weights = {'sales_z': self.sales_weight, 'sga_inv_z': self.sga_weight, 'gpm_z': self.gpm_weight}
        df[TurnaroundCols.stage3_score] = compute_composite_score(df, weights)
        df.drop(columns=['sales_z', 'sga_inv_z', 'gpm_z'], inplace=True)
        
        # 🔴 버그 1 수정: 행을 삭제하지 않고 fail_reason 컬럼으로 이력 관리
        df['fail_reason'] = None
        
        na_reasons_str = df[TurnaroundCols.na_reasons].astype(str)
        is_data_short = na_reasons_str.str.contains('DATA_TOO_SHORT', na=False)
        is_exempt = na_reasons_str.str.contains('TURNAROUND_NOT_COMPUTABLE', na=False)
        
        # 구제 대상 점수 NaN 처리
        df.loc[is_exempt, TurnaroundCols.stage3_score] = np.nan
        
        # 데이터 부족 종목 기계적 탈락 처리
        df.loc[is_data_short, 'fail_reason'] = FailReason.DATA_TOO_SHORT.value

        # 점수 기반 컷오프 산출 (데이터 부족 및 구제 대상 제외)
        valid_mask = ~is_data_short & ~is_exempt
        if valid_mask.any():
            cutoff_val = df.loc[valid_mask, TurnaroundCols.stage3_score].quantile(1.0 - self.pass_percentile)
            
            # 컷오프 미달 탈락 처리
            is_below_cutoff = valid_mask & (df[TurnaroundCols.stage3_score] < cutoff_val)
            df.loc[is_below_cutoff, 'fail_reason'] = FailReason.COMPOSITE_SCORE_BELOW_CUTOFF.value

        # Status 로깅을 위한 변수 할당
        passed_count = df['fail_reason'].isnull().sum()
        self.logger.info(f"[Stage 3] {len(df)}개 종목 중 {passed_count}개 턴어라운드 종목 통과 (상위 {self.pass_percentile*100}%)")
        
        # ---------------------------------------------------------
        # 4. 스키마 무결성 검증 (출하 전 최종 확인)
        # ---------------------------------------------------------
        validate_schema(df, TurnaroundMetrics)
        
        return df