import pandas as pd
import numpy as np
import logging

class FinancialHealthScreener:
    """
    5단계: 재무 건전성
    상환 능력(이자보상배율)과 이익의 질(현금흐름)을 확인하여 
    안정적으로 사업을 영위할 수 있는 종목인지 최종 검증합니다.
    """
    def __init__(self, params: dict):
        self.debt_ratio_cutoff = params.get('debt_ratio_percentile_cutoff', 0.5)
        self.icr_min = params.get('interest_coverage_min', 1.5)
        
        self.logger = logging.getLogger(__name__)

    def run(self, input_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        """
        input_df: 4단계를 통과한 종목 정보 (ticker, sector 등 포함)
        """
        metrics_data = []

        # 1. 원자료 조달 및 재무 건전성 지표 계산
        for _, row in input_df.iterrows():
            ticker = row['ticker']
            sector = row['sector']

            # TTM 재무 데이터 호출 (Flow는 합산, Stock은 스냅샷)
            raw_ttm = loader.get_ttm_financials(ticker, base_date)

            # --- 부채비율 계산 (총부채 / 총자본) ---
            total_liab = raw_ttm.get('total_liabilities', np.nan)
            total_equity = raw_ttm.get('total_equity', np.nan)
            debt_ratio = np.divide(total_liab, total_equity) if pd.notna(total_liab) and pd.notna(total_equity) else np.nan

            # --- 현금흐름 및 이익 품질 계산 ---
            ocf = raw_ttm.get('operating_cash_flow', np.nan)
            ni = raw_ttm.get('net_income', np.nan)

            # --- 이자보상배율 계산 ---
            op_inc = raw_ttm.get('operating_income', np.nan)
            int_exp = raw_ttm.get('interest_expense', np.nan)

            na_reasons = []
            icr = np.nan

            if pd.notna(op_inc) and pd.notna(int_exp):
                if int_exp <= 0:
                    # 무차입 경영이거나 이자수익이 더 커서 이자비용이 0 이하인 경우
                    # 상환 능력이 무한대(inf)인 초우량 상태로 간주
                    icr = np.inf
                else:
                    icr = np.divide(op_inc, int_exp)

            # 금융업 특수성 태깅 (예수금 등이 부채로 잡혀 부채비율이 무의미함)
            if any(keyword in str(sector) for keyword in ['금융', '증권', '보험', '은행', '지주']):
                na_reasons.append("DEBT_RATIO_CAUTION")

            metrics_data.append({
                'ticker': ticker,
                'sector': sector,
                'debt_ratio': debt_ratio,
                'ocf': ocf,
                'net_income': ni,
                'interest_coverage_ratio': icr,
                'na_reasons': ",".join(na_reasons)
            })

        df = pd.DataFrame(metrics_data)

        # ---------------------------------------------------------
        # 2. 섹터 내 부채비율 상대 순위 계산
        # ---------------------------------------------------------
        # 부채비율은 낮을수록 건전하므로 오름차순 랭킹 부여
        df['debt_rank'] = df.groupby('sector')['debt_ratio'].rank(pct=True, ascending=True)

        # ---------------------------------------------------------
        # 3. 필터링 조건 적용
        # ---------------------------------------------------------
        # 조건 1: 부채비율이 섹터 내에서 일정 수준 이하일 것 (단, 금융업은 예외 통과)
        cond_debt = df['debt_rank'] <= self.debt_ratio_cutoff
        cond_debt_exempt = df['na_reasons'].astype(str).str.contains('DEBT_RATIO_CAUTION', na=False)

        # 조건 2: OCF(영업활동현금흐름) 흑자이면서 당기순이익보다 클 것 (발생액 품질 검증)
        # 당기순이익이 결측치인 경우 조건 비교 오류 방지를 위해 -inf로 채움
        cond_ocf = (df['ocf'] > 0) & (df['ocf'] > df['net_income'].fillna(-np.inf))

        # 조건 3: 이자보상배율이 기준치(예: 1.5배) 이상일 것
        cond_icr = df['interest_coverage_ratio'] >= self.icr_min

        passed_df = df[(cond_debt | cond_debt_exempt) & cond_ocf & cond_icr].copy()

        self.logger.info(f"[Stage 5] {len(df)}개 종목 중 {len(passed_df)}개 재무 건전성 통과")
        
        schema_columns = ['ticker', 'sector', 'debt_ratio', 'ocf', 'net_income', 'interest_coverage_ratio', 'na_reasons']
        return passed_df[schema_columns]