import pandas as pd
import numpy as np
import logging
from dateutil.relativedelta import relativedelta

class ValuationScreener:
    """
    4단계: 밸류에이션
    저평가 상태(PBR)인지 확인하되, BPS 훼손 여부와 ROE를 페어링하여 
    정당한 저평가(Value Trap)와 기회인 저평가를 구분합니다.
    """
    def __init__(self, params: dict):
        self.pbr_cutoff = params.get('pbr_percentile_cutoff', 0.5)
        self.bps_growth_min = params.get('bps_growth_yoy_min', 0.0)
        self.roe_trap_threshold = params.get('roe_value_trap_threshold', 0.05)
        
        self.logger = logging.getLogger(__name__)

    def run(self, input_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        """
        input_df: 이전 단계를 통과한 종목 정보 (ticker, sector, 그리고 stage2에서 계산된 'roe' 포함 필수)
        """
        # 1. pykrx 원자료 조달 (현재 시점 및 1년 전 시점)
        date_1y_ago = base_date - relativedelta(years=1)
        
        fund_t0 = loader.get_market_fundamental_cross_section(base_date)
        fund_t4 = loader.get_market_fundamental_cross_section(date_1y_ago)
        
        # 1년 전 BPS 컬럼명 변경 (병합을 위해)
        fund_t4 = fund_t4[['ticker', 'bps']].rename(columns={'bps': 'bps_1y_ago'})
        
        # 데이터 병합 (입력 df + 현재 펀더멘털 + 과거 펀더멘털)
        df = pd.merge(input_df, fund_t0, on='ticker', how='left')
        df = pd.merge(df, fund_t4, on='ticker', how='left')
        
        na_reasons = []

        # 2. BPS Growth YoY 계산
        # 0으로 나누는 것을 방지
        df['bps_growth_yoy'] = np.where(
            (pd.notna(df['bps'])) & (pd.notna(df['bps_1y_ago'])) & (df['bps_1y_ago'] != 0),
            (df['bps'] - df['bps_1y_ago']) / np.abs(df['bps_1y_ago']),
            np.nan
        )

        # 3. 섹터 내 PBR 상대 순위(Percentile) 계산
        # PBR은 낮을수록 좋으므로 오름차순으로 순위를 매김
        df['pbr_rank'] = df.groupby('sector')['pbr'].rank(pct=True, ascending=True)

        # 4. 밸류트랩(Value Trap) 감지 로직
        # PBR이 섹터 내 하위 50%로 낮지만, ROE도 기준치(예: 5%) 미만으로 형편없는 경우
        df['is_pbr_value_trap'] = (df['pbr_rank'] <= self.pbr_cutoff) & (df['roe'] < self.roe_trap_threshold)

        # 5. 필터링 조건 적용
        # 조건 1: PBR이 일정 순위 이내일 것 (또는 자본잠식으로 PBR이 잡히지 않는 경우 예외 처리)
        cond_pbr = df['pbr_rank'] <= self.pbr_cutoff
        cond_pbr_exempt = df['pbr'].isna() | (df['pbr'] <= 0) # 자본잠식 종목 (우선 통과시키고 5단계에서 거름)
        
        # 조건 2: BPS가 전년 대비 훼손되지 않았을 것 (빅배스/손상차손 등 장부가치 하락 방어)
        cond_bps = df['bps_growth_yoy'] >= self.bps_growth_min
        
        passed_df = df[(cond_pbr | cond_pbr_exempt) & cond_bps].copy()

        self.logger.info(f"[Stage 4] {len(df)}개 종목 중 {len(passed_df)}개 저평가 종목 통과")
        
        schema_columns = ['ticker', 'sector', 'pbr', 'bps_growth_yoy', 'is_pbr_value_trap']
        return passed_df[schema_columns]