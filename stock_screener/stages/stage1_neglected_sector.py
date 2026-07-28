import pandas as pd
import numpy as np
import logging
from typing import Dict, List

class NeglectedSectorScreener:
    """
    1단계: 소외 섹터 발굴 (Neglected Firm Effect)
    시장의 관심에서 벗어나 있는 섹터를 1차로 넓게 후보군에 포함시킵니다.
    """
    
    def __init__(self, params: dict):
        # params.yaml에서 주입받을 파라미터들 (하드코딩 배제)
        self.w_return = params.get('stage1_return_weight', 0.5)
        self.w_volume = params.get('stage1_volume_weight', 0.5)
        self.pass_ratio = params.get('stage1_pass_ratio', 0.4) # 상위 30~40% 통과용
        self.logger = logging.getLogger(__name__)

    def run(self, sector_df: pd.DataFrame) -> pd.DataFrame:
        """
        섹터별 시계열 데이터프레임을 받아 소외 섹터를 필터링합니다.
        
        [입력 데이터프레임 (sector_df) 요구사항]
        - sector: 섹터명
        - return_1m: 최근 1개월 수익률
        - return_6m: 최근 6개월 수익률
        - vol_prop_1m: 최근 1개월 시장 전체 대비 거래대금 비중
        - vol_prop_1y: 최근 1년 시장 전체 대비 거래대금 비중 (또는 전년 동기)
        """
        df = sector_df.copy()

        # ---------------------------------------------------------
        # 1. 수익률 소외도 계산 (Return Neglect)
        # ---------------------------------------------------------
        # 코스피 평균(또는 시가총액 가중 평균) 대비 초과수익률의 z-score 계산
        # 소외될수록(수익률이 낮을수록) 높은 점수를 부여하기 위해 -1을 곱함
        df['return_z_score'] = -((df['return_6m'] - df['return_6m'].mean()) / df['return_6m'].std())

        # ---------------------------------------------------------
        # 2. 거래 소외도 계산 (Volume Neglect)
        # ---------------------------------------------------------
        # 시장 전체 대비 비중 변화로 정규화 (최근 1개월 비중 / 1년 평균 비중)
        # 거래대금 비중이 줄어들수록(소외될수록) 높은 점수를 부여하기 위해 -1을 곱해 z-score 계산
        df['vol_prop_ratio'] = df['vol_prop_1m'] / df['vol_prop_1y'].replace(0, np.nan)
        df['volume_z_score'] = -((df['vol_prop_ratio'] - df['vol_prop_ratio'].mean()) / df['vol_prop_ratio'].std())

        # ---------------------------------------------------------
        # 3. 종합 소외도 점수 (Composite Score)
        # ---------------------------------------------------------
        df['composite_score'] = (self.w_return * df['return_z_score']) + (self.w_volume * df['volume_z_score'])

        # ---------------------------------------------------------
        # 4. 밸류트랩 최소 방어선 (Value Trap Warning)
        # ---------------------------------------------------------
        # 낙폭 가속 필터: 최근 1개월 낙폭이 6개월 평균 낙폭보다 더 가팔라지는지 확인
        # 6개월 수익률을 6으로 나누어 단순 월평균 낙폭 산출 (복리 적용 전 단순 비교)
        avg_monthly_drop_6m = df['return_6m'] / 6.0
        
        # 1개월 수익률이 음수이면서, 6개월 평균 월간 하락폭보다 더 크게 하락한 경우
        df['is_value_trap_warning'] = (df['return_1m'] < 0) & (df['return_1m'] < avg_monthly_drop_6m)

        # ---------------------------------------------------------
        # 5. 최종 후보군 필터링 (Top Percentile)
        # ---------------------------------------------------------
        # 상위 N% 통과 비율 적용
        cutoff_rank = int(len(df) * self.pass_ratio)
        df['rank'] = df['composite_score'].rank(ascending=False, method='min')
        
        passed_sectors = df[df['rank'] <= cutoff_rank].copy()
        
        # 스키마 매핑 형식에 맞춰 반환 컬럼 정리
        schema_columns = [
            'sector', 'return_z_score', 'volume_z_score', 
            'composite_score', 'is_value_trap_warning'
        ]
        
        self.logger.info(f"[Stage 1] 총 {len(df)}개 섹터 중 {len(passed_sectors)}개 섹터 통과 (Pass Ratio: {self.pass_ratio*100}%)")
        
        return passed_sectors[schema_columns].sort_values(by='composite_score', ascending=False)