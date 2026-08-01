import pandas as pd
import logging
from typing import Dict, Any, Tuple
from datetime import date

# 구현된 5개 스테이지 임포트
from stages.stage1_neglected_sector import NeglectedSectorScreener
from stages.stage2_sector_leaders import SectorLeaderScreener
from stages.stage3_fundamental_improve import FundamentalImproveScreener
from stages.stage4_valuation import ValuationScreener
from stages.stage5_financial_health import FinancialHealthScreener

class QuantPipeline:
    """
    종목 선별 파이프라인 오케스트레이터.
    - 데이터의 조달은 loader에 위임합니다.
    - 지표의 계산 및 통과/탈락 판정은 각 stage에 위임합니다.
    - 본 클래스는 조건문 기반의 계산 로직을 갖지 않으며, 오직 실행 순서와 이력(history)만 관리합니다.
    """
    def __init__(self, params: Dict[str, Any], loader):
        self.params = params
        self.loader = loader
        self.logger = logging.getLogger(__name__)
        
        # 각 스테이지 초기화 (params.yaml의 해당 블록 주입)
        self.stage1 = NeglectedSectorScreener(params.get('stage1_neglected_sector', {}))
        self.stage2 = SectorLeaderScreener(params.get('stage2_sector_leaders', {}))
        self.stage3 = FundamentalImproveScreener(params.get('stage3_fundamental_improve', {}))
        self.stage4 = ValuationScreener(params.get('stage4_valuation', {}))
        self.stage5 = FinancialHealthScreener(params.get('stage5_financial_health', {}))

    def _accumulate_results(self, input_df: pd.DataFrame, stage_result_df: pd.DataFrame) -> pd.DataFrame:
        """
        [핵심 로직] 이전 단계의 데이터를 유실하지 않도록, 통과한 종목(ticker)을 기준으로 
        기존 컬럼(input_df)과 새로 계산된 컬럼(stage_result_df)을 안전하게 병합합니다.
        """
        if stage_result_df.empty:
            return pd.DataFrame()
            
        # 중복되는 컬럼(예: sector) 충돌 방지: ticker만 남기고 교집합 제외
        cols_to_use = stage_result_df.columns.difference(input_df.columns).tolist()
        cols_to_use.append('ticker')
        
        # 교집합인 ticker를 기준으로 inner merge (통과한 종목만 남으면서 이전 데이터 누적)
        return pd.merge(input_df, stage_result_df[cols_to_use], on='ticker', how='inner')
    
    def run(self, base_date: date) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
        """
        파이프라인을 실행합니다.
        
        Returns:
            final_df (pd.DataFrame): 1~5단계를 모두 통과한 최종 종목 리스트
            history (Dict): 사후 분석을 위해 각 단계별 통과 결과를 담은 딕셔너리
        """
        self.logger.info(f"========== [Pipeline Start] Base Date: {base_date} ==========")
        history = {}

        # ---------------------------------------------------------
        # 0. 원자료 조달 (유니버스 및 섹터 지표)
        # ---------------------------------------------------------
        # loader 내부에서 point-in-time 검증 및 데이터 조달 수행
        universe_df = self.loader.get_kospi_universe(base_date)
        sector_metrics_df = self.loader.get_sector_metrics(base_date)
        
        # ---------------------------------------------------------
        # 1. Stage 1: 소외 섹터 발굴
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 1: Neglected Sector")
        passed_sectors_df = self.stage1.run(sector_metrics_df)
        history['stage1'] = passed_sectors_df
        
        if passed_sectors_df.empty:
            self.logger.warning("Stage 1에서 통과한 섹터가 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # 통과한 섹터에 속하는 종목들만 유니버스에서 추출하여 Stage 2로 전달
        passed_sectors_list = passed_sectors_df['sector'].tolist()
        stage1_passed_tickers = universe_df[universe_df['sector'].isin(passed_sectors_list)].copy()
        
        # ---------------------------------------------------------
        # 2. Stage 2: 섹터 내 우량주 탐색
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 2: Sector Leaders")
        stage2_raw = self.stage2.run(stage1_passed_tickers, self.loader, base_date)
        passed_stage2_df = self._accumulate_results(stage1_passed_tickers, stage2_raw)
        history['stage2'] = passed_stage2_df
        
        if passed_stage2_df.empty:
            self.logger.warning("Stage 2에서 통과한 종목이 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # ---------------------------------------------------------
        # 3. Stage 3: 체질 개선 (Turnaround)
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 3: Fundamental Improve")
        stage3_raw = self.stage3.run(passed_stage2_df, self.loader, base_date)
        passed_stage3_df = self._accumulate_results(passed_stage2_df, stage3_raw)
        history['stage3'] = passed_stage3_df
        
        if passed_stage3_df.empty:
            self.logger.warning("Stage 3에서 통과한 종목이 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # ---------------------------------------------------------
        # 4. Stage 4: 밸류에이션
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 4: Valuation")
        stage4_raw = self.stage4.run(passed_stage3_df, self.loader, base_date)
        passed_stage4_df = self._accumulate_results(passed_stage3_df, stage4_raw)
        history['stage4'] = passed_stage4_df
        
        if passed_stage4_df.empty:
            self.logger.warning("Stage 4에서 통과한 종목이 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # ---------------------------------------------------------
        # 5. Stage 5: 재무 건전성
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 5: Financial Health")
        stage5_raw = self.stage5.run(passed_stage4_df, self.loader, base_date)
        final_df = self._accumulate_results(passed_stage4_df, stage5_raw)
        
        self.logger.info(f"========== [Pipeline End] Final Passed: {len(final_df)} ==========")
        
        return final_df, history