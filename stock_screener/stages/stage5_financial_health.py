import pandas as pd
import numpy as np
import logging
from core import metrics_utils

class FinancialHealthScreener:
    """
    5단계: 재무 건전성 
    metrics_utils의 순수 통계 함수를 활용하여 Z-score 스코어링 및 상대평가를 진행합니다.
    결격 사유는 Warning Tag로 관리하여 우량 성장주의 억울한 탈락을 방지합니다.
    """
    def __init__(self, params: dict):
        # YAML 파라미터 매핑
        self.cutoff_quantile = params.get('stage5_cutoff_quantile', 0.2)
        self.icr_weight = params.get('icr_weight', 0.7)
        self.debt_weight = params.get('debt_weight', 0.3)
        
        self.logger = logging.getLogger(__name__)

    def run(self, input_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        if input_df.empty: return input_df

        metrics_data = []

        # 1. 원자료 조달 및 Raw 지표 계산
        for _, row in input_df.iterrows():
            ticker = row['ticker']
            sector = row.get('sector', 'N/A')
            raw_ttm = loader.get_ttm_financials(ticker, base_date)

            total_liab = raw_ttm.get('total_liabilities', np.nan)
            total_equity = raw_ttm.get('total_equity', np.nan)
            debt_ratio = np.divide(total_liab, total_equity) if pd.notna(total_liab) and pd.notna(total_equity) else np.nan

            ocf = raw_ttm.get('operating_cash_flow', np.nan)
            ni = raw_ttm.get('net_income', np.nan)

            op_inc = raw_ttm.get('operating_income', np.nan)
            int_exp = raw_ttm.get('interest_expense', np.nan)

            warning_tags = []
            na_reasons = []
            icr = np.nan

            if pd.notna(op_inc) and pd.notna(int_exp):
                if int_exp <= 0:
                    icr = 50.0  # Z-score 계산을 위해 상단 캡핑
                else:
                    icr = np.divide(op_inc, int_exp)

            if any(keyword in str(sector) for keyword in ['금융', '증권', '보험', '은행', '지주']):
                na_reasons.append(metrics_utils.MetricStatus.CAUTION)

            metrics_data.append({
                'ticker': ticker,
                'sector': sector,
                'debt_ratio': debt_ratio,
                'ocf': ocf,
                'net_income': ni,
                'interest_coverage_ratio': icr,
                'na_reasons': ",".join(na_reasons),
                'warning_tags': ""
            })

        df = pd.DataFrame(metrics_data)

        # ---------------------------------------------------------
        # 2. Warning Tag 부착 (이상치 처리 및 로직 기반 태깅)
        # ---------------------------------------------------------
        df['icr_capped'] = df['interest_coverage_ratio'].clip(lower=-10, upper=50)
        df['debt_ratio_capped'] = df['debt_ratio'].clip(upper=5)

        df.loc[df['interest_coverage_ratio'] < 1.0, 'warning_tags'] += "[ICR미달]"
        df.loc[(df['ocf'] < df['net_income'].fillna(-np.inf)), 'warning_tags'] += "[이익질주의]"
        
        df['debt_rank_pct'] = df.groupby('sector')['debt_ratio'].rank(pct=True, ascending=True)
        cond_not_finance = ~df['na_reasons'].astype(str).str.contains(metrics_utils.MetricStatus.CAUTION)
        df.loc[cond_not_finance & (df['debt_rank_pct'] > 0.90), 'warning_tags'] += "[과다부채]"

        # ---------------------------------------------------------
        # 3. metrics_utils를 활용한 Z-score 및 합산 점수 계산
        # ---------------------------------------------------------
        df['debt_ratio_inv'] = -df['debt_ratio_capped']

        df['icr_zscore'] = metrics_utils.calc_zscore(df['icr_capped'])
        df['debt_zscore'] = metrics_utils.calc_zscore(df['debt_ratio_inv'])

        # YAML 설정 가중치 적용
        score_mapping = {'icr_zscore': self.icr_weight, 'debt_zscore': self.debt_weight}
        df['stage5_score'] = metrics_utils.compute_composite_score(df, score_mapping)

        cond_finance = df['na_reasons'].str.contains(metrics_utils.MetricStatus.CAUTION)
        df.loc[cond_finance, 'stage5_score'] = df.loc[cond_finance, 'icr_zscore']

        # ---------------------------------------------------------
        # 4. 상대 평가 필터링 (하위 20% 탈락)
        # ---------------------------------------------------------
        top_percentile = 1.0 - self.cutoff_quantile 

        if len(df) > 5:
            passed_df, threshold = metrics_utils.apply_percentile_filter(df, 'stage5_score', top_percentile)
        else:
            passed_df = df.copy()
            threshold = 0.0

        self.logger.info(f"[Stage 5] {len(df)}개 중 {len(passed_df)}개 생존 (Threshold: {threshold:.3f})")
        
        # 이전 스테이지 데이터 보존
        schema_columns = [
            'ticker', 'sector', 'debt_ratio', 'ocf', 'net_income', 
            'interest_coverage_ratio', 'stage5_score', 'warning_tags', 'na_reasons'
        ]
        final_cols = list(set(input_df.columns.tolist() + schema_columns))
        return passed_df[[c for c in final_cols if c in passed_df.columns]]