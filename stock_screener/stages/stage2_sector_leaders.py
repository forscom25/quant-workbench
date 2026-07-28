import pandas as pd
import logging

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
        self.logger = logging.getLogger(__name__)

    def run(self, sector_tickers_df: pd.DataFrame, financials_df: pd.DataFrame) -> pd.DataFrame:
        """
        sector_tickers_df: 1단계를 통과한 섹터에 속한 종목들의 기본 정보 (ticker, sector, mcap 등)
        financials_df: 해당 종목들의 TTM 기반 재무 지표 (roe, roic, op_margin_std, na_reasons 등)
        """
        # 종목 정보와 재무 지표 병합
        df = pd.merge(sector_tickers_df, financials_df, on='ticker', how='inner')
        
        # ---------------------------------------------------------
        # 1. 섹터 내 상대 순위(Percentile) 계산
        # ---------------------------------------------------------
        # ROE, ROIC는 높을수록 상위 랭크 (1.0에 가까울수록 좋음)
        df['roe_rank'] = df.groupby('sector')['roe'].rank(pct=True, ascending=True)
        df['roic_rank'] = df.groupby('sector')['roic'].rank(pct=True, ascending=True)
        
        # 영업이익률 변동성은 낮을수록 상위 랭크 (오름차순 랭크 적용 후 뒤집기)
        df['op_vol_rank'] = df.groupby('sector')['op_margin_std'].rank(pct=True, ascending=False)

        # ---------------------------------------------------------
        # 2. 필터링 조건 적용
        # ---------------------------------------------------------
        cond_roe = df['roe_rank'] >= (1 - self.roe_cutoff)
        cond_op_vol = df['op_vol_rank'] >= (1 - self.op_vol_cutoff)

        # ROIC 조건 + 금융/지주사 등 ROIC 계산 불가 업종 예외 처리
        # financials_df에 결측 사유가 기록된 'na_reasons' 컬럼이 있다고 가정
        cond_roic = df['roic_rank'] >= (1 - self.roic_cutoff)
        cond_roic_exempt = df['na_reasons'].astype(str).str.contains('NOT_COMPUTABLE', na=False)

        # 최종 조건 결합
        passed_df = df[cond_roe & (cond_roic | cond_roic_exempt) & cond_op_vol].copy()

        self.logger.info(f"[Stage 2] {len(df)}개 종목 중 {len(passed_df)}개 우량주 통과")
        
        # 반환할 컬럼 정리 (필요에 따라 확장)
        schema_columns = ['ticker', 'sector', 'roe', 'roic', 'op_margin_std', 'na_reasons']
        return passed_df[schema_columns]