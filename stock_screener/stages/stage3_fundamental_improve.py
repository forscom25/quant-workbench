import pandas as pd
import numpy as np
import logging
from core.metrics_utils import calc_zscore, compute_composite_score, apply_percentile_filter

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
        self.sales_decline_threshold = params.get('cost_cutting_only_sales_decline_threshold', -0.05)
        
        self.logger = logging.getLogger(__name__)

    def run(self, sector_tickers_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        metrics_data = []

        for _, row in sector_tickers_df.iterrows():
            ticker = row['ticker']
            sector = row['sector']
            
            # 1. 시계열 원자료 조달
            q_series = loader.get_quarterly_financials_series(ticker, base_date, n_quarters=self.lookback_q)
            
            na_reasons = []
            
            if len(q_series) < 6 or all(pd.isna(q_series[0].get('revenue', np.nan)) for _ in range(6)):
                na_reasons.append("DATA_TOO_SHORT")
                metrics_data.append({
                    'ticker': ticker, 'sector': sector,
                    'sga_yoy_avg': np.nan, 'sales_growth_yoy': np.nan, 'gpm_yoy': np.nan,
                    'na_reasons': ",".join(na_reasons)
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
                na_reasons.append("TURNAROUND_NOT_COMPUTABLE")
            
            gpm_t0 = safe_div(gp_t0, rev_t0)
            gpm_t4 = safe_div(q_series[4].get('gross_profit', np.nan), rev_t4)
            gpm_yoy = gpm_t0 - gpm_t4

            metrics_data.append({
                'ticker': ticker, 'sector': sector,
                'sga_yoy_avg': sga_yoy_avg,
                'sales_growth_yoy': sales_growth_yoy,
                'gpm_yoy': gpm_yoy,
                'na_reasons': ",".join(na_reasons)
            })

        df = pd.DataFrame(metrics_data)
        if df.empty: return df

        # ---------------------------------------------------------
        # 3. Composite Score 스코어링 및 필터링
        # ---------------------------------------------------------
        df['sales_z'] = calc_zscore(df['sales_growth_yoy'])
        df['sga_inv_z'] = calc_zscore(-df['sga_yoy_avg']) # 판관비 증가는 나쁘므로 부호 반전
        df['gpm_z'] = calc_zscore(df['gpm_yoy'])
        
        weights = {'sales_z': self.sales_weight, 'sga_inv_z': self.sga_weight, 'gpm_z': self.gpm_weight}
        df['stage3_score'] = compute_composite_score(df, weights)
        
        # 구제 대상 태깅 (NOT_COMPUTABLE)
        is_exempt = df['na_reasons'].astype(str).str.contains('TURNAROUND_NOT_COMPUTABLE', na=False)
        df.loc[is_exempt, 'stage3_score'] = np.nan
        
        # 경고 태그 (매출 역성장)
        df['is_cost_cutting_warning'] = df['sales_growth_yoy'] <= self.sales_decline_threshold
        
        # 퍼센타일 컷오프 (결측치 구제 포함)
        passed_df, _ = apply_percentile_filter(df, 'stage3_score', self.pass_percentile)
        
        # Status 로깅 (선택적)
        passed_df.loc[passed_df['stage3_score'].isna(), 'stage3_status'] = 'EXEMPT'
        passed_df.loc[passed_df['stage3_score'].notna(), 'stage3_status'] = 'PASSED'

        self.logger.info(f"[Stage 3] {len(df)}개 종목 중 {len(passed_df)}개 턴어라운드 종목 통과 (상위 {self.pass_percentile*100}%)")
        return passed_df