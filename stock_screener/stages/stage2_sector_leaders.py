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

        # 2026-09-11 도입: ROE/ROIC를 TTM 단일값이 아니라 연간 확정치의 "수준+추세" 블렌딩으로
        # 계산할지 여부(pipeline.py가 global.profitability_basis를 이 dict에 주입해 전달).
        # "ttm"(기본값)이면 기존 동작과 완전히 동일 — 이 값이 Stage2 필터링 로직을 바꾸는
        # 유일한 지점이며, 나머지 단계(Stage3/4/5)는 이 설정과 무관하게 항상 TTM을 쓴다.
        self.profitability_basis = params.get('profitability_basis', 'ttm')
        self.annual_trend_lookback_years = params.get('annual_trend_lookback_years', 3)
        self.annual_level_weight = params.get('annual_level_weight', 0.5)
        self.annual_trend_weight = params.get('annual_trend_weight', 0.5)

        self.logger = logging.getLogger(__name__)

    def _compute_roe_roic(self, data: dict) -> tuple:
        """단일 시점(TTM 또는 특정 연도) 원자료에서 ROE/ROIC와 ROIC 분모를 계산한다."""
        roe = np.divide(data.get('net_income', np.nan), data.get('total_equity', np.nan))

        roic_num = data.get('operating_income', np.nan) * (1 - self.effective_tax_rate)
        # 투하자본 = 이자부 차입금(total_borrowings) + 자기자본(total_equity).
        total_borrowings = data.get('total_borrowings', np.nan)
        total_borrowings = 0.0 if pd.isna(total_borrowings) else total_borrowings
        roic_den = total_borrowings + data.get('total_equity', np.nan)
        roic = np.divide(roic_num, roic_den)

        return roe, roic, roic_den

    def _compute_annual_blended_roe_roic(self, annual_series: list) -> tuple:
        """
        연간 확정치 시계열(최신순)로부터 ROE/ROIC를 "수준(최근년도 값) + 추세(최근년도-최고년도
        변화량)"의 가중합산으로 계산한다. 레벨과 추세 모두 같은 단위(ROE/ROIC 비율)라 z-score
        없이 바로 가중평균한다 — 이미 우량한 안정주(추세는 평평해도 수준이 높음)와 턴어라운드주
        (수준은 낮아도 추세가 가파름)를 둘 다 반영하기 위한 설계(2026-09-11 사용자 논의 결론).
        연간 데이터가 1개년치만 있으면(신규상장 등) 추세를 계산할 수 없으므로 수준값만 사용하고,
        0개년치면(공시 자체를 못 찾음) ROIC_NOT_COMPUTABLE 등 기존 결측 처리 경로로 자연히 흡수된다.
        """
        if not annual_series:
            return np.nan, np.nan, np.nan

        # annual_series는 최신순([(연도, dict), ...]) — [0]이 가장 최근 확정 연도
        roe_latest, roic_latest, roic_den_latest = self._compute_roe_roic(annual_series[0][1])

        if len(annual_series) < 2:
            return roe_latest, roic_latest, roic_den_latest

        roe_oldest, roic_oldest, _ = self._compute_roe_roic(annual_series[-1][1])

        roe_trend = roe_latest - roe_oldest if pd.notna(roe_latest) and pd.notna(roe_oldest) else np.nan
        roic_trend = roic_latest - roic_oldest if pd.notna(roic_latest) and pd.notna(roic_oldest) else np.nan

        roe_blended = (
            self.annual_level_weight * roe_latest + self.annual_trend_weight * roe_trend
            if pd.notna(roe_latest) and pd.notna(roe_trend) else roe_latest
        )
        roic_blended = (
            self.annual_level_weight * roic_latest + self.annual_trend_weight * roic_trend
            if pd.notna(roic_latest) and pd.notna(roic_trend) else roic_latest
        )
        return roe_blended, roic_blended, roic_den_latest

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
            # ROE/ROIC 계산 기준: "ttm"(기본값)이면 분자(흐름)-분모(스냅샷) 조합을 TTM 단일
            # 시점으로 계산(과거 동작과 동일), "annual"이면 최근 N개년 확정 사업보고서의
            # 수준+추세 블렌딩으로 계산(2026-09-11 도입, ROIC 분모 계산식 자체는 두 경로가 동일).
            if self.profitability_basis == "annual":
                annual_series = loader.get_annual_financials_series(
                    ticker, base_date, n_years=self.annual_trend_lookback_years
                )
                roe, roic, roic_den = self._compute_annual_blended_roe_roic(annual_series)
            else:
                raw_ttm = loader.get_ttm_financials(ticker, base_date)
                roe, roic, roic_den = self._compute_roe_roic(raw_ttm)

            op_margin_series = loader.get_quarterly_op_margin_series(ticker, base_date, n_quarters=self.op_margin_lookback_q)

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