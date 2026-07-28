import logging
from typing import List, Dict
import pandas as pd

from core.schema import StockProfile, MetricStatus
from data.loader import QuantDataLoader
from stages.stage1_neglected_sector import NeglectedSectorScreener
from stages.stage2_sector_leaders import SectorLeaderScreener

class QuantPipeline:
    """
    모든 Stage의 실행 순서를 지휘하고, 데이터와 상태(Schema)를 관리하는 오케스트레이터
    """
    def __init__(self, params: dict, loader: QuantDataLoader):
        self.params = params
        self.loader = loader
        self.logger = logging.getLogger(__name__)
        
        # 1. 각 Stage 초기화 (yaml 파라미터 주입)
        self.stage1 = NeglectedSectorScreener(params.get('stage1_neglected_sector', {}))
        self.stage2 = SectorLeaderScreener(params.get('stage2_sector_leaders', {}))
        # self.stage3 = ...

    def run(self, base_date) -> List[StockProfile]:
        self.logger.info(f"🚀 파이프라인 실행 시작 (기준일: {base_date})")
        
        # [데이터 조달] 유니버스 로드
        universe_df = self.loader.get_kospi_universe(base_date)
        
        # ---------------------------------------------------------
        # 깔때기 1: Stage 1 (섹터 단위 필터링)
        # ---------------------------------------------------------
        # (생략: loader를 통해 섹터별 수익률/거래대금 df 생성 로직)
        sector_data_df = pd.DataFrame() # 임시 빈 데이터프레임
        passed_sectors_df = self.stage1.run(sector_data_df)
        passed_sector_names = passed_sectors_df['sector'].tolist()
        
        # Stage 1을 통과한 섹터의 종목들만 StockProfile 객체로 인스턴스화
        candidates: List[StockProfile] = []
        for _, row in universe_df[universe_df['sector'].isin(passed_sector_names)].iterrows():
            profile = StockProfile(ticker=row['ticker'], name=row['name'], sector=row['sector'])
            profile.advance_stage() # Stage 1 통과 마킹
            candidates.append(profile)
            
        self.logger.info(f"✅ Stage 1 통과: {len(candidates)}개 종목이 다음 단계로 진입합니다.")

        # ---------------------------------------------------------
        # 깔때기 2: Stage 2 (종목 단위 필터링 & TTM 계산 주입)
        # ---------------------------------------------------------
        active_candidates = [c for c in candidates if c.status.name == 'PASS']
        
        # 1. 살아남은 종목들에 대해서만 TTM 데이터(Raw) 로드 및 변환
        stage2_input_data = []
        for stock in active_candidates:
            try:
                # 파이프라인이 Loader를 호출하여 TTM 계산 로직 수행 (여기에 TTM 병합 로직 위치)
                # ttm_data = self._calculate_ttm(stock.ticker, base_date)
                ttm_data = {'roe': 0.15, 'roic': 0.12, 'op_margin_std': 0.03, 'na_reasons': {}} # 임시 데이터
                
                # 계산된 데이터를 schema(StockProfile)에 저장
                stock.quality_metrics = ttm_data
                
                stage2_input_data.append({
                    'ticker': stock.ticker,
                    'sector': stock.sector,
                    **ttm_data
                })
            except Exception as e:
                # [정책] 데이터 조달 실패 시 처리
                stock.mark_failed(fail_reason=f"Data Load Error: {e}")
                
        # 2. Stage 2 실행
        stage2_df = pd.DataFrame(stage2_input_data)
        passed_stage2_df = self.stage2.run(
            sector_tickers_df=pd.DataFrame([{'ticker': c.ticker, 'sector': c.sector} for c in active_candidates]),
            financials_df=stage2_df
        )
        passed_stage2_tickers = passed_stage2_df['ticker'].tolist()
        
        # 3. 결과에 따라 상태(Schema) 업데이트
        for stock in active_candidates:
            if stock.ticker in passed_stage2_tickers:
                stock.advance_stage()
            else:
                stock.mark_failed(fail_reason="Stage 2 필터링 탈락 (ROE/ROIC/변동성 미달)")

        # ---------------------------------------------------------
        # 깔때기 3: Stage 3... (반복)
        # ---------------------------------------------------------
        
        # 최종 통과 종목 반환
        final_survivors = [c for c in candidates if c.status.name == 'PASS']
        self.logger.info(f"🏁 파이프라인 종료. 최종 생존 종목: {len(final_survivors)}개")
        
        return candidates # 탈락한 종목도 사유 분석을 위해 함께 반환하는 것이 일반적입니다.