import pandas as pd
import logging
from typing import Dict, Any, Tuple
from datetime import date
from core.schema import Stage, FailReason

# 구현된 5개 스테이지 임포트
from stages.stage1_neglected_sector import NeglectedSectorScreener
from stages.stage2_sector_leaders import SectorLeaderScreener
from stages.stage3_fundamental_improve import FundamentalImproveScreener
from stages.stage4_valuation import ValuationScreener
from stages.stage5_financial_health import FinancialHealthScreener

class QuantPipeline:
    """
    종목 선별 파이프라인 오케스트레이터.
    - 데이터 조달은 loader에, 판정은 각 stage에 위임합니다.
    - fail_reason 컬럼을 통해 탈락 사유를 로깅하고, 통과한 종목만 다음 단계로 넘깁니다.
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
        [핵심 로직] 이전 단계의 데이터를 유실하지 않도록, ticker를 기준으로
        기존 컬럼(input_df)과 새로 계산된 컬럼(stage_result_df)을 안전하게 병합합니다.
        """
        if stage_result_df.empty:
            return pd.DataFrame()

        # 'fail_reason'은 매 단계 새로 판정되는 값이므로, input_df에 남아있는 이전 값
        # (필터링을 통과한 입력이라 항상 None)을 지우고 이번 stage의 판정으로 교체한다.
        base_df = input_df.drop(columns=['fail_reason'], errors='ignore')

        # 중복되는 컬럼(예: sector) 충돌 방지: ticker만 남기고 교집합 제외
        cols_to_use = stage_result_df.columns.difference(base_df.columns).tolist()
        cols_to_use.append('ticker')

        # ticker를 기준으로 inner merge하여 이전 데이터를 누적
        return pd.merge(base_df, stage_result_df[cols_to_use], on='ticker', how='inner')

    def _filter_passed(self, df: pd.DataFrame) -> pd.DataFrame:
        """fail_reason이 None(결측치)인 종목(통과 종목)만 추출하여 복사본 반환"""
        if 'fail_reason' not in df.columns:
            return df.copy()
        return df[df['fail_reason'].isnull()].copy()
    
    def run(self, base_date: date, stop_after: Stage | None = None) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
        """
        파이프라인을 순차적으로 실행합니다.
        - stop_after: 지정된 Stage까지만 실행 후 결과를 조기 반환합니다. None일 경우 전체 실행.
        
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
        sector_result_df = self.stage1.run(sector_metrics_df)

        if sector_result_df.empty:
            self.logger.warning("Stage 1에서 처리할 섹터 데이터가 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # 섹터 단위 판정을 티커 단위로 전개: 섹터가 탈락하면 소속 티커 전원이
        # SECTOR_NOT_QUALIFIED를 상속받는다 (섹터 자체의 컷오프 탈락 사유와는 별개 개념).
        sector_extra_cols = sector_result_df.columns.difference(['fail_reason']).tolist()
        sector_status = sector_result_df[sector_extra_cols + ['fail_reason']].rename(
            columns={'fail_reason': 'sector_fail_reason'}
        )
        stage1_ticker_df = pd.merge(universe_df, sector_status, on='sector', how='left')

        stage1_ticker_df['fail_reason'] = None
        stage1_ticker_df.loc[
            stage1_ticker_df['sector_fail_reason'].notna(), 'fail_reason'
        ] = FailReason.SECTOR_NOT_QUALIFIED.value
        stage1_ticker_df.drop(columns=['sector_fail_reason'], inplace=True)

        history['stage1'] = stage1_ticker_df
        stage1_passed_tickers = self._filter_passed(stage1_ticker_df)

        if stage1_passed_tickers.empty:
            self.logger.warning("Stage 1에서 통과한 종목이 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # 🚨 [추가] Stage 1 이후 조기 종료
        if stop_after is not None and stop_after == Stage.STAGE1:
            return stage1_passed_tickers, history
        
        # ---------------------------------------------------------
        # 2. Stage 2: 섹터 내 우량주 탐색
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 2: Sector Leaders")
        stage2_raw = self.stage2.run(stage1_passed_tickers, self.loader, base_date)
        accumulated_stage2 = self._accumulate_results(stage1_passed_tickers, stage2_raw)

        # 전체 이력(탈락 포함) 저장 후 통과 종목만 추출
        history['stage2'] = accumulated_stage2
        passed_stage2_df = self._filter_passed(accumulated_stage2)
        
        if passed_stage2_df.empty:
            self.logger.warning("Stage 2에서 통과한 종목이 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history
        
        # 🚨 [추가] Stage 2 이후 조기 종료
        if stop_after is not None and stop_after == Stage.STAGE2:
            return passed_stage2_df, history

        # ---------------------------------------------------------
        # 3. Stage 3: 체질 개선 (Turnaround)
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 3: Fundamental Improve")
        stage3_raw = self.stage3.run(passed_stage2_df, self.loader, base_date)
        accumulated_stage3 = self._accumulate_results(passed_stage2_df, stage3_raw)

        history['stage3'] = accumulated_stage3
        passed_stage3_df = self._filter_passed(accumulated_stage3)
        
        if passed_stage3_df.empty:
            self.logger.warning("Stage 3에서 통과한 종목이 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # 🚨 [추가] Stage 3 이후 조기 종료
        if stop_after is not None and stop_after == Stage.STAGE3:
            return passed_stage3_df, history

        # ---------------------------------------------------------
        # 4. Stage 4: 밸류에이션
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 4: Valuation")
        stage4_raw = self.stage4.run(passed_stage3_df, self.loader, base_date)
        accumulated_stage4 = self._accumulate_results(passed_stage3_df, stage4_raw)
        
        history['stage4'] = accumulated_stage4
        passed_stage4_df = self._filter_passed(accumulated_stage4)

        if passed_stage4_df.empty:
            self.logger.warning("Stage 4에서 통과한 종목이 없습니다. 파이프라인을 종료합니다.")
            return pd.DataFrame(), history

        # 🚨 [추가] Stage 4 이후 조기 종료
        if stop_after is not None and stop_after == Stage.STAGE4:
            return passed_stage4_df, history

        # ---------------------------------------------------------
        # 5. Stage 5: 재무 건전성
        # ---------------------------------------------------------
        self.logger.info(">>> Running Stage 5: Financial Health")
        stage5_raw = self.stage5.run(passed_stage4_df, self.loader, base_date)
        final_accumulated = self._accumulate_results(passed_stage4_df, stage5_raw)

        history['stage5'] = final_accumulated
        final_df = self._filter_passed(final_accumulated)
        
        self.logger.info(f"========== [Pipeline End] Final Passed: {len(final_df)} ==========")
        
        return final_df, history