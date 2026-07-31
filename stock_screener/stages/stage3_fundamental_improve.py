import pandas as pd
import numpy as np
import logging

class FundamentalImproveScreener:
    """
    3단계: 체질 개선 (Turnaround)
    판관비율 감소, 재고회전율 상승, GPM(매출총이익률) 개선 등 
    실제 펀더멘털이 개선되고 있는지(가짜 턴어라운드가 아닌지) 검증합니다.
    """
    def __init__(self, params: dict):
        self.lookback_q = params.get('sga_lookback_quarters', 6)
        self.req_sales_growth = params.get('require_sales_growth', True)
        self.req_gpm_up = params.get('require_gpm_improvement', True)
        self.req_inv_up = params.get('require_inventory_turnover_up', True)
        
        self.logger = logging.getLogger(__name__)

    def run(self, sector_tickers_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        metrics_data = []

        for _, row in sector_tickers_df.iterrows():
            ticker = row['ticker']
            sector = row['sector']
            
            # 1. 시계열 원자료 조달 (인덱스 0: 최근 분기, 1: 직전 분기, 4: 전년동기, 5: 직전분기의 전년동기)
            q_series = loader.get_quarterly_financials_series(ticker, base_date, n_quarters=self.lookback_q)
            
            na_reasons = []
            
            # 데이터 부족 시 처리
            if len(q_series) < 6 or all(pd.isna(q_series[0].get('revenue', np.nan)) for _ in range(6)):
                na_reasons.append("DATA_TOO_SHORT")
                metrics_data.append({'ticker': ticker, 'sector': sector, 'na_reasons': ",".join(na_reasons)})
                continue

            # 2. 지표 계산 함수 (안전한 나눗셈)
            def safe_div(n, d):
                return np.divide(n, d) if pd.notna(n) and pd.notna(d) and d != 0 else np.nan

            # --- 판관비율 (SGA Ratio) ---
            sga_ratio_t0 = safe_div(q_series[0].get('sga'), q_series[0].get('revenue'))
            sga_ratio_t4 = safe_div(q_series[4].get('sga'), q_series[4].get('revenue'))
            sga_ratio_t1 = safe_div(q_series[1].get('sga'), q_series[1].get('revenue'))
            sga_ratio_t5 = safe_div(q_series[5].get('sga'), q_series[5].get('revenue'))

            sga_ratio_yoy_q1 = sga_ratio_t0 - sga_ratio_t4 # 최근 분기 판관비율 YoY
            sga_ratio_yoy_q2 = sga_ratio_t1 - sga_ratio_t5 # 직전 분기 판관비율 YoY
            is_sga_dec_consecutively = (sga_ratio_yoy_q1 < 0) and (sga_ratio_yoy_q2 < 0)

            # --- 매출액 성장률 (Sales Growth YoY) ---
            rev_t0 = q_series[0].get('revenue', np.nan)
            rev_t4 = q_series[4].get('revenue', np.nan)
            sales_growth_yoy = safe_div(rev_t0 - rev_t4, abs(rev_t4))

            # --- 재고회전율 (Inventory Turnover) 및 매출총이익률 (GPM) ---
            inv_t0 = q_series[0].get('inventory', np.nan)
            gp_t0 = q_series[0].get('gross_profit', np.nan)
            
            # 금융업 등 재고나 매출원가(GPM) 개념이 없는 업종 태깅
            if pd.isna(inv_t0) or pd.isna(gp_t0):
                na_reasons.append("TURNAROUND_NOT_COMPUTABLE")
            
            inv_turnover_t0 = safe_div(rev_t0, inv_t0)
            inv_turnover_t4 = safe_div(rev_t4, q_series[4].get('inventory', np.nan))
            inventory_turnover_yoy = inv_turnover_t0 - inv_turnover_t4

            gpm_t0 = safe_div(gp_t0, rev_t0)
            gpm_t4 = safe_div(q_series[4].get('gross_profit', np.nan), rev_t4)
            gpm_yoy = gpm_t0 - gpm_t4

            # 지주사 등 특정 섹터 주의 태깅 (매출 성장 해석 주의)
            if '지주' in str(sector):
                na_reasons.append("SALES_GROWTH_CAUTION")

            metrics_data.append({
                'ticker': ticker,
                'sector': sector,
                'sga_ratio_yoy_q1': sga_ratio_yoy_q1,
                'sga_ratio_yoy_q2': sga_ratio_yoy_q2,
                'is_sga_decreasing_consecutively': is_sga_dec_consecutively,
                'sales_growth_yoy': sales_growth_yoy,
                'inventory_turnover_yoy': inventory_turnover_yoy,
                'gpm_yoy': gpm_yoy,
                'na_reasons': ",".join(na_reasons)
            })

        df = pd.DataFrame(metrics_data)

        # ---------------------------------------------------------
        # 3. 필터링 조건 적용
        # ---------------------------------------------------------
        # 기본 조건: 2개 분기 연속 판관비율 YoY 감소
        cond_sga = df['is_sga_decreasing_consecutively'] == True

        # 선택 조건: 매출 성장
        cond_sales = (df['sales_growth_yoy'] > 0) if self.req_sales_growth else True
        
        # 선택 조건: 재고회전율 개선 (증가)
        cond_inv = (df['inventory_turnover_yoy'] > 0) if self.req_inv_up else True
        
        # 선택 조건: GPM 유지/개선 (하락폭이 0 이상)
        cond_gpm = (df['gpm_yoy'] >= 0) if self.req_gpm_up else True

        # 예외 처리: 금융업 등 계산 불가 업종은 해당 지표 필터링 면제 (Pass)
        cond_exempt = df['na_reasons'].astype(str).str.contains('TURNAROUND_NOT_COMPUTABLE', na=False)

        passed_df = df[
            cond_sga & 
            (cond_sales | cond_exempt) & 
            (cond_inv | cond_exempt) & 
            (cond_gpm | cond_exempt)
        ].copy()

        self.logger.info(f"[Stage 3] {len(df)}개 종목 중 {len(passed_df)}개 턴어라운드 종목 통과")
        
        return passed_df