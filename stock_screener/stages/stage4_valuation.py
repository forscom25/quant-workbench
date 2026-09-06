import pandas as pd
import numpy as np
import logging
from dateutil.relativedelta import relativedelta
from core.metrics_utils import calc_zscore, compute_composite_score, apply_percentile_filter
from core.schema import (
    ValuationCols,
    ValuationMetrics,
    FailReason,
    validate_schema
)

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
        self.zscore_clip_lower = params.get('zscore_clip_lower', 0.01)
        self.zscore_clip_upper = params.get('zscore_clip_upper', 0.99)

        self.logger = logging.getLogger(__name__)

    def run(self, input_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        if input_df.empty: return input_df

        date_1y_ago = base_date - relativedelta(years=1)

        # 1. 펀더멘털 데이터 로드 (loader 내부에서 영업일 보정됨)
        fund_t0 = loader.get_market_fundamental_cross_section(base_date)
        fund_t4 = loader.get_market_fundamental_cross_section(date_1y_ago)

        # pykrx 네트워크 순단 등으로 loader가 빈 DataFrame(컬럼 없음)을 반환하면
        # .str 접근자 자체가 AttributeError를 던져 전체 백테스트가 죽는다 — 이번 분기만
        # 밸류에이션 컬럼을 결측 처리(기존 PBR-NaN 구제 경로로 자연히 흡수됨)하고 계속 진행.
        if fund_t0.empty or fund_t4.empty:
            self.logger.error(
                f"[Stage 4] {base_date} 기준 밸류에이션 원자료 조달 실패(pykrx 응답 없음) — "
                f"이번 분기는 전 종목 밸류에이션 지표를 결측(구제) 처리합니다."
            )
            fund_t0 = pd.DataFrame(columns=['ticker', 'bps', 'pbr', 'per'])
            fund_t4 = pd.DataFrame(columns=['ticker', 'bps'])
        else:
            # 컬럼명을 소문자로 통일 (pykrx 대문자 출력 대응)
            fund_t0.columns = fund_t0.columns.str.lower()
            fund_t4.columns = fund_t4.columns.str.lower()

        # 1년 전 BPS 추출
        fund_t4 = fund_t4[['ticker', 'bps']].rename(columns={'bps': 'bps_1y_ago'})

        # 기존 통과 데이터에 병합 (fund_t0에 pbr/bps/per가 함께 실려옴)
        df = pd.merge(input_df, fund_t0, on='ticker', how='left')
        df = pd.merge(df, fund_t4, on='ticker', how='left')

        # 안전망: loader 응답에 per 컬럼이 없는 극단적 실패 상황에서도 스키마를 만족시키기 위함
        # (정상 흐름에서는 fund_t0 merge로 이미 실제 값이 채워져 있어야 함)
        if ValuationCols.per not in df.columns:
            df[ValuationCols.per] = np.nan

        # 2. BPS 성장률 계산 (스키마 규격에 맞춰 컬럼명 'bps_growth' 사용)
        df[ValuationCols.bps_growth] = np.where(
            (pd.notna(df['bps'])) & (pd.notna(df['bps_1y_ago'])) & (df['bps_1y_ago'] != 0),
            (df['bps'] - df['bps_1y_ago']) / np.abs(df['bps_1y_ago']),
            np.nan
        )

        # ---------------------------------------------------------
        # 3. Composite Score 스코어링 및 필터링
        # ---------------------------------------------------------
        # PBR 역수 처리 (자본잠식 등 0 이하 값은 np.nan 처리하여 구제)
        safe_pbr = df[ValuationCols.pbr].apply(lambda x: x if pd.notna(x) and x > 0 else np.nan)
        df['pbr_inv_z'] = calc_zscore(1 / safe_pbr, self.zscore_clip_lower, self.zscore_clip_upper)
        df['bps_z'] = calc_zscore(df[ValuationCols.bps_growth], self.zscore_clip_lower, self.zscore_clip_upper)
        
        weights = {'pbr_inv_z': self.pbr_weight, 'bps_z': self.bps_weight}
        df[ValuationCols.stage4_score] = compute_composite_score(df, weights)
        df.drop(columns=['pbr_inv_z', 'bps_z'], inplace=True)

        # 밸류 트랩 경고 태그 (이전 단계에서 roe 컬럼이 넘어왔다고 가정)
        roe_series = df.get('roe', pd.Series(0, index=df.index))
        df['is_pbr_value_trap'] = (df[ValuationCols.stage4_score] > 0) & (roe_series < self.trap_roe_threshold)

        # ---------------------------------------------------------
        # 4. FailReason 태깅 로직 (Row 삭제 안 함)
        # ---------------------------------------------------------
        df['fail_reason'] = None
        
        # 자본잠식 등 PBR 결측치 구제 대상 마킹 (점수는 무효화하지만 탈락시키지는 않음)
        is_exempt = df[ValuationCols.pbr].isna() | (df[ValuationCols.pbr] <= 0)
        df.loc[is_exempt, ValuationCols.stage4_score] = np.nan

        valid_mask = ~is_exempt
        if valid_mask.any():
            cutoff_val = df.loc[valid_mask, ValuationCols.stage4_score].quantile(1.0 - self.pass_percentile)
            
            # 컷오프 미달 탈락 처리
            is_below_cutoff = valid_mask & (df[ValuationCols.stage4_score] < cutoff_val)
            df.loc[is_below_cutoff, 'fail_reason'] = FailReason.COMPOSITE_SCORE_BELOW_CUTOFF.value

        # 로깅
        passed_count = df['fail_reason'].isnull().sum()
        self.logger.info(f"[Stage 4] {len(df)}개 종목 중 {passed_count}개 저평가 종목 통과 (상위 {self.pass_percentile*100}%)")
        
        # ---------------------------------------------------------
        # 5. 스키마 무결성 검증 (출하 전 최종 확인)
        # ---------------------------------------------------------
        validate_schema(df, ValuationMetrics)
        
        return df