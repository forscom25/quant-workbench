import pandas as pd
import numpy as np
import logging
# [수정] utils 및 Status 추가 임포트
from core.metrics_utils import compute_std, MetricStatus
from core.schema import QualityCols, QualityMetrics, FailReason, validate_schema

class SectorLeaderScreener:
    """
    2단계: 섹터 내 우량주 탐색
    1단계를 통과한 소외 섹터 내에서 수익성(ROE/ROIC)이 높고 
    실적 변동성이 낮은 종목을 선별합니다.
    """
    def __init__(self, params: dict):
        self.roe_cutoff = params.get('roe_percentile_cutoff', 0.5)
        self.roic_cutoff = params.get('roic_percentile_cutoff', 0.5)
        self.op_vol_cutoff = params.get('op_margin_std_percentile_cutoff', 0.5)

        # [수정] 시계열 통계 계산용 파라미터 추가
        self.op_margin_lookback_q = params.get('op_margin_lookback_q', 8)
        self.op_margin_min_quarters = params.get('op_margin_min_quarters', 4)
        self.effective_tax_rate = params.get('effective_tax_rate', 0.22)

        self.logger = logging.getLogger(__name__)

    # [수정] run 메서드 시그니처 변경 (미리 계산된 df 대신 loader를 직접 받음)
    def run(self, sector_tickers_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        """
        sector_tickers_df: 1단계를 통과한 섹터에 속한 종목 정보 (ticker, sector 등)
        loader: 원자료를 조달하기 위한 QuantDataLoader 인스턴스
        base_date: 공시 시차 검증 및 TTM 산출을 위한 기준일자
        """
        metrics_data = []
        # [수정] 1. 원자료 조달 및 지표 직접 계산 (Stage의 핵심 역할)
        for _, row in sector_tickers_df.iterrows():
            ticker = row['ticker']
            sector = row['sector']
            
            # Loader에게 원자료(Atomic Data) 요청
            raw_ttm = loader.get_ttm_financials(ticker, base_date)
            op_margin_series = loader.get_quarterly_op_margin_series(ticker, base_date, n_quarters=self.op_margin_lookback_q)
            
            # 비율 계산 (ROE, ROIC) - ZeroDivisionError 등을 막기 위해 np.divide 사용
            roe = np.divide(raw_ttm.get('net_income', np.nan), raw_ttm.get('total_equity', np.nan))
            
            roic_num = raw_ttm.get('operating_income', np.nan) * (1 - self.effective_tax_rate)
            # 투하자본 = 이자부 차입금(total_borrowings) + 자기자본(total_equity).
            # (과거 total_assets - total_liabilities로 계산했으나 이는 회계항등식상 자기자본과
            # 동일해 실질적으로 ROIC가 아닌 ROE의 변형이었음 — data/cache 실데이터로 확인 후 수정)
            total_borrowings = raw_ttm.get('total_borrowings', np.nan)
            total_borrowings = 0.0 if pd.isna(total_borrowings) else total_borrowings
            roic_den = total_borrowings + raw_ttm.get('total_equity', np.nan)
            roic = np.divide(roic_num, roic_den)
            
            # 영업이익률 변동성(표준편차) 계산 (utils 활용)
            op_std, op_std_status = compute_std(op_margin_series, min_valid_points=self.op_margin_min_quarters)
            
            # 예외 사유(na_reasons) 태깅
            na_reasons = []
            if op_std_status == MetricStatus.NOT_COMPUTABLE:
                na_reasons.append("OP_MARGIN_STD_NOT_COMPUTABLE")
            if pd.isna(roic_den) or roic_den <= 0:
                na_reasons.append("ROIC_NOT_COMPUTABLE") # 투하자본 음수 등

            metrics_data.append({
                'ticker': ticker,
                'sector': sector,
                'roe': roe,
                'roic': roic,
                'op_margin_std': op_std,
                'na_reasons': ",".join(na_reasons)
            })

        df = pd.DataFrame(metrics_data)

        # ---------------------------------------------------------
        # 2. 섹터 내 상대 순위(Percentile) 계산
        # ---------------------------------------------------------
        # ROE, ROIC는 높을수록 상위 랭크 (1.0에 가까울수록 좋음)
        df['roe_rank'] = df.groupby('sector')['roe'].rank(pct=True, ascending=True)
        df['roic_rank'] = df.groupby('sector')['roic'].rank(pct=True, ascending=True)
        
        # 영업이익률 변동성은 낮을수록 상위 랭크 (오름차순 랭크 적용 후 뒤집기)
        df['op_vol_rank'] = df.groupby('sector')['op_margin_std'].rank(pct=True, ascending=False)

        # ---------------------------------------------------------
        # 3. 필터링 조건 적용
        # ---------------------------------------------------------
        cond_roe = df['roe_rank'] >= (1 - self.roe_cutoff)
        cond_op_vol = df['op_vol_rank'] >= (1 - self.op_vol_cutoff)

        # ROIC 조건 + 금융/지주사 등 ROIC 계산 불가 업종 예외 처리
        # financials_df에 결측 사유가 기록된 'na_reasons' 컬럼이 있다고 가정
        cond_roic = df['roic_rank'] >= (1 - self.roic_cutoff)
        cond_roic_exempt = df['na_reasons'].astype(str).str.contains('ROIC_NOT_COMPUTABLE', na=False)

        # [추가] 예외 처리: 신규 상장 등으로 영업이익률 시계열이 부족한 종목 구제
        cond_op_vol_exempt = df['na_reasons'].astype(str).str.contains('OP_MARGIN_STD_NOT_COMPUTABLE', na=False)

        # 최종 조건 결합 — 탈락 종목도 fail_reason 태깅 후 보존 (row 삭제 안 함)
        is_passed = cond_roe & (cond_roic | cond_roic_exempt) & (cond_op_vol | cond_op_vol_exempt)
        df[QualityCols.fail_reason] = None
        df.loc[~is_passed, QualityCols.fail_reason] = FailReason.QUALITY_CUTOFF_NOT_MET.value

        passed_count = df[QualityCols.fail_reason].isnull().sum()
        self.logger.info(f"[Stage 2] {len(df)}개 종목 중 {passed_count}개 우량주 통과")

        # 반환할 컬럼 정리 (필요에 따라 확장)
        schema_columns = ['ticker', 'sector', 'roe', 'roic', 'op_margin_std', 'na_reasons', 'fail_reason']
        result = df[schema_columns]

        validate_schema(result, QualityMetrics)
        return result