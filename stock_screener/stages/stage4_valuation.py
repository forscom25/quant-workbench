import pandas as pd
import numpy as np
import logging
from dateutil.relativedelta import relativedelta
from core.metrics_utils import calc_zscore, compute_composite_score, apply_percentile_filter

class ValuationScreener:
    """
    4단계: 밸류에이션
    저평가 상태(PBR)인지 확인하되, BPS 훼손 여부와 ROE를 페어링하여 
    정당한 저평가(Value Trap)와 기회인 저평가를 구분합니다.
    """
    def __init__(self, params: dict):
        # [수정] YAML에서 받아올 가중치 및 통과 기준
        self.pbr_weight = params.get('pbr_weight', 0.5)
        self.bps_weight = params.get('bps_growth_weight', 0.5)
        self.pass_percentile = params.get('composite_pass_percentile', 0.3)
        self.trap_roe_threshold = params.get('value_trap_roe_threshold', 0.05)
        
        self.logger = logging.getLogger(__name__)

    def run(self, input_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        if input_df.empty: return input_df

        date_1y_ago = base_date - relativedelta(years=1)
        fund_t0 = loader.get_market_fundamental_cross_section(base_date)
        fund_t4 = loader.get_market_fundamental_cross_section(date_1y_ago)
        
        fund_t4 = fund_t4[['ticker', 'bps']].rename(columns={'bps': 'bps_1y_ago'})
        
        df = pd.merge(input_df, fund_t0, on='ticker', how='left')
        df = pd.merge(df, fund_t4, on='ticker', how='left')

        df['bps_growth_yoy'] = np.where(
            (pd.notna(df['bps'])) & (pd.notna(df['bps_1y_ago'])) & (df['bps_1y_ago'] != 0),
            (df['bps'] - df['bps_1y_ago']) / np.abs(df['bps_1y_ago']),
            np.nan
        )

        # ---------------------------------------------------------
        # 3. Composite Score 스코어링 및 필터링
        # ---------------------------------------------------------
        # PBR 역수 처리 (자본잠식, 0 이하 값 처리)
        safe_pbr = df['pbr'].apply(lambda x: x if pd.notna(x) and x > 0 else np.nan)
        df['pbr_inv_z'] = calc_zscore(1 / safe_pbr)
        df['bps_z'] = calc_zscore(df['bps_growth_yoy'])
        
        weights = {'pbr_inv_z': self.pbr_weight, 'bps_z': self.bps_weight}
        df['stage4_score'] = compute_composite_score(df, weights)

        # 밸류 트랩 경고 태그 (탈락이 아님)
        df['is_pbr_value_trap'] = (df['pbr_inv_z'] > 0) & (df['roe'] < self.trap_roe_threshold)

        # 자본잠식 등 PBR 결측치 구제 대상 마킹
        is_exempt = df['pbr'].isna() | (df['pbr'] <= 0)
        df.loc[is_exempt, 'stage4_score'] = np.nan

        # 퍼센타일 컷오프 (결측치 구제 포함)
        passed_df, _ = apply_percentile_filter(df, 'stage4_score', self.pass_percentile)
        
        # Status 로깅
        passed_df.loc[passed_df['stage4_score'].isna(), 'stage4_status'] = 'EXEMPT'
        passed_df.loc[passed_df['stage4_score'].notna(), 'stage4_status'] = 'PASSED'

        self.logger.info(f"[Stage 4] {len(df)}개 종목 중 {len(passed_df)}개 저평가 종목 통과 (상위 {self.pass_percentile*100}%)")
        
        # 반환 스키마 유지
        schema_columns = ['ticker', 'sector', 'pbr', 'bps_growth_yoy', 'is_pbr_value_trap', 'stage4_status']
        # 기존 input_df의 컬럼을 유실하지 않도록 컬럼 교집합 유지
        final_cols = list(set(input_df.columns.tolist() + schema_columns))
        
        return passed_df[[c for c in final_cols if c in passed_df.columns]]