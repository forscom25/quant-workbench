import pandas as pd
import numpy as np
import logging
from core.metrics_utils import calc_zscore, compute_composite_score, apply_percentile_filter

from core.schema import (
    TurnaroundCols,
    TurnaroundMetrics,
    FailReason,
    validate_schema
)

class FundamentalImproveScreener:
    """
    3단계: 체질 개선 (Turnaround)
    판관비율 감소, 재고회전율 상승, GPM(매출총이익률) 개선 등 
    실제 펀더멘털이 개선되고 있는지(가짜 턴어라운드가 아닌지) 검증합니다.
    """
    def __init__(self, params: dict):
        self.lookback_q = params.get('sga_lookback_quarters', 6)

        # [수정] YAML에서 받아올 가중치 및 통과 기준
        self.sales_weight = params.get('sales_weight', 0.4)
        self.sga_weight = params.get('sga_weight', 0.4)
        self.gpm_weight = params.get('gpm_weight', 0.2)
        self.pass_percentile = params.get('composite_pass_percentile', 0.3)
        self.sales_decline_threshold = params.get('cost_cutting_only_sales_decline_threshold', 0.00)
        self.zscore_clip_lower = params.get('zscore_clip_lower', 0.01)
        self.zscore_clip_upper = params.get('zscore_clip_upper', 0.99)

        # 현금흐름 구제(penalty 상쇄) 설정 — "매출은 느는데 판관비가 더 빨리 늘어 컷오프 미달"인
        # 종목이라도, 실제 매출 성장이 있고(순수 테마 배제) OCF가 건실하면(회계상 숫자놀음이 아니라
        # 진짜 현금이 들어오면) 기계적 탈락시키지 않고 구제한다. 방산 등 확정 수주 기반으로 설비/인력에
        # 선제 투자하는 업종이, 아직 마진에 반영 안 됐다는 이유만으로 걸러지는 걸 막기 위함.
        self.cash_flow_rescue_enabled = params.get('cash_flow_rescue_enabled', True)
        self.cash_flow_rescue_min_sales_growth = params.get('cash_flow_rescue_min_sales_growth', 0.0)

        self.logger = logging.getLogger(__name__)

    def run(self, sector_tickers_df: pd.DataFrame, loader, base_date) -> pd.DataFrame:
        metrics_data = []

        for _, row in sector_tickers_df.iterrows():
            ticker = row['ticker']
            sector = row['sector']
            
            # 1. 시계열 원자료 조달
            q_series = loader.get_quarterly_financials_series(ticker, base_date, n_quarters=self.lookback_q)
            
            # 2026-09-10: dict -> comma-joined string으로 통일(Stage2/4/5와 동일 타입).
            # MetricStatus/설명 문구는 어느 stage에서도 na_reasons에서 다시 읽힌 적이 없어(항상
            # .str.contains()로 태그명만 확인) 손실 없이 단순화 가능함을 확인 후 진행.
            na_reasons = []

            # 🔴 버그 2 수정: q_series[0]만 반복 검사하던 논리 오류 해결
            if len(q_series) < 6 or all(pd.isna(q.get('revenue', np.nan)) for q in q_series[:6]):
                na_reasons.append('DATA_TOO_SHORT')
                metrics_data.append({
                    'ticker': ticker,
                    'sector': sector,
                    TurnaroundCols.sga_yoy_avg: np.nan,
                    TurnaroundCols.sales_growth_yoy: np.nan,
                    TurnaroundCols.gpm_yoy: np.nan,
                    TurnaroundCols.stage3_score: np.nan,
                    TurnaroundCols.is_cost_cutting_warning: False,
                    TurnaroundCols.inventory_turnover_yoy: np.nan, # 스키마 요구사항(Optional)
                    TurnaroundCols.na_reasons: ",".join(na_reasons),
                    TurnaroundCols.ocf: np.nan,
                    TurnaroundCols.net_income: np.nan
                })
                continue

            def safe_div(n, d):
                return np.divide(n, d) if pd.notna(n) and pd.notna(d) and d != 0 else np.nan

            # --- 판관비액 (SGA) ---
            # [수정] 단순 비율(Ratio) 감소 연속성이 아닌, 2개 분기 판관비 '증감률'의 평균 사용
            sga_t0 = q_series[0].get('sga')
            sga_t4 = q_series[4].get('sga')
            sga_t1 = q_series[1].get('sga')
            sga_t5 = q_series[5].get('sga')
            
            sga_yoy_q1 = safe_div(sga_t0 - sga_t4, abs(sga_t4)) if pd.notna(sga_t4) else np.nan
            sga_yoy_q2 = safe_div(sga_t1 - sga_t5, abs(sga_t5)) if pd.notna(sga_t5) else np.nan
            sga_yoy_avg = np.nanmean([sga_yoy_q1, sga_yoy_q2]) if pd.notna(sga_yoy_q1) or pd.notna(sga_yoy_q2) else np.nan

            # --- 매출액 성장률 (Sales Growth YoY) ---
            rev_t0 = q_series[0].get('revenue', np.nan)
            rev_t4 = q_series[4].get('revenue', np.nan)
            sales_growth_yoy = safe_div(rev_t0 - rev_t4, abs(rev_t4))

            # --- 매출총이익률 (GPM) ---
            gp_t0 = q_series[0].get('gross_profit', np.nan)
            if pd.isna(gp_t0):
                na_reasons.append('TURNAROUND_NOT_COMPUTABLE')
            
            gpm_t0 = safe_div(gp_t0, rev_t0)
            gpm_t4 = safe_div(q_series[4].get('gross_profit', np.nan), rev_t4)
            gpm_yoy = gpm_t0 - gpm_t4

            # 경고 태그 (매출 역성장)
            is_cost_cutting_warning = bool(pd.notna(sales_growth_yoy) and sales_growth_yoy <= self.sales_decline_threshold)

            # --- 현금흐름 구제 판단용 원자료 (TTM) ---
            # Stage5가 어차피 같은 티커에 대해 이 값을 다시 조회하므로, 대부분 캐시에서 즉시 반환됨
            ocf = net_income = np.nan
            if self.cash_flow_rescue_enabled:
                raw_ttm = loader.get_ttm_financials(ticker, base_date)
                ocf = raw_ttm.get('operating_cash_flow', np.nan)
                net_income = raw_ttm.get('net_income', np.nan)

                cash_flow_healthy = pd.notna(ocf) and pd.notna(net_income) and ocf > 0 and ocf >= net_income
                real_growth = pd.notna(sales_growth_yoy) and sales_growth_yoy > self.cash_flow_rescue_min_sales_growth
                if cash_flow_healthy and real_growth:
                    na_reasons.append('CASH_FLOW_QUALITY_RESCUE')

            metrics_data.append({
                'ticker': ticker,
                'sector': sector,
                TurnaroundCols.sga_yoy_avg: sga_yoy_avg,
                TurnaroundCols.sales_growth_yoy: sales_growth_yoy,
                TurnaroundCols.ocf: ocf,
                TurnaroundCols.net_income: net_income,
                TurnaroundCols.gpm_yoy: gpm_yoy,
                TurnaroundCols.stage3_score: np.nan,
                TurnaroundCols.is_cost_cutting_warning: is_cost_cutting_warning,
                TurnaroundCols.inventory_turnover_yoy: np.nan,
                TurnaroundCols.na_reasons: ",".join(na_reasons)
            })

        df = pd.DataFrame(metrics_data)
        if df.empty: return df

        # ---------------------------------------------------------
        # 3. Composite Score 스코어링 및 필터링
        # ---------------------------------------------------------
        df['sales_z'] = calc_zscore(df[TurnaroundCols.sales_growth_yoy], self.zscore_clip_lower, self.zscore_clip_upper)
        df['sga_inv_z'] = calc_zscore(-df[TurnaroundCols.sga_yoy_avg], self.zscore_clip_lower, self.zscore_clip_upper) # 판관비 증가는 나쁘므로 부호 반전
        df['gpm_z'] = calc_zscore(df[TurnaroundCols.gpm_yoy], self.zscore_clip_lower, self.zscore_clip_upper)
        
        weights = {'sales_z': self.sales_weight, 'sga_inv_z': self.sga_weight, 'gpm_z': self.gpm_weight}
        df[TurnaroundCols.stage3_score] = compute_composite_score(df, weights)
        df.drop(columns=['sales_z', 'sga_inv_z', 'gpm_z'], inplace=True)
        
        # 🔴 버그 1 수정: 행을 삭제하지 않고 fail_reason 컬럼으로 이력 관리
        df['fail_reason'] = None
        
        na_reasons_str = df[TurnaroundCols.na_reasons]
        is_data_short = na_reasons_str.str.contains('DATA_TOO_SHORT', na=False)
        # TURNAROUND_NOT_COMPUTABLE(GPM 계산 불가 업종)만 컷오프 계산 대상에서 제외한다.
        # CASH_FLOW_QUALITY_RESCUE는 여기 포함하지 않음 — 랭킹 모집단에서 미리 빼버리면 컷오프
        # 문턱값 자체가 달라져 버려서, 구제가 필요 없던 종목까지 의도치 않게 영향을 줌. 대신
        # 정상적으로 랭킹에 참여시킨 뒤, 컷오프 미달로 탈락한 종목 중 구제 태그가 있는 것만
        # 아래에서 별도로 되살린다.
        is_exempt = na_reasons_str.str.contains('TURNAROUND_NOT_COMPUTABLE', na=False)

        # 🔴 2026-09-09 발견 버그 수정: is_data_short/is_exempt 어느 쪽으로도 명시 처리되지
        # 않은 결측 경로(예: 4분기 전 매출/판관비/매출원가만 결측)로 sales_z/sga_inv_z/gpm_z
        # 중 하나가 NaN이 되면 덧셈으로 stage3_score 전체가 NaN이 되는데, 이후 컷오프 비교
        # `NaN < cutoff_val`은 항상 False라 아무 태그 없이 조용히 통과해버렸다. 발견 당시
        # 통과 종목이 있다는 사실만으로는 감사가 불가능했던 문제 — 명시적으로 태깅해 다른
        # 구제 경로와 동일하게 컷오프 계산에서도 제외한다.
        score_nan_unexplained = df[TurnaroundCols.stage3_score].isna() & ~is_data_short & ~is_exempt
        has_existing_tag = score_nan_unexplained & (df[TurnaroundCols.na_reasons] != "")
        has_no_tag = score_nan_unexplained & (df[TurnaroundCols.na_reasons] == "")
        df.loc[has_existing_tag, TurnaroundCols.na_reasons] += ",SCORE_NOT_COMPUTABLE"
        df.loc[has_no_tag, TurnaroundCols.na_reasons] = "SCORE_NOT_COMPUTABLE"
        is_exempt = is_exempt | score_nan_unexplained

        # 구제 대상 점수 NaN 처리
        df.loc[is_exempt, TurnaroundCols.stage3_score] = np.nan

        # 데이터 부족 종목 기계적 탈락 처리
        df.loc[is_data_short, 'fail_reason'] = FailReason.DATA_TOO_SHORT.value

        # 점수 기반 컷오프 산출 (데이터 부족 및 구제 대상 제외)
        valid_mask = ~is_data_short & ~is_exempt
        if valid_mask.any():
            cutoff_val = df.loc[valid_mask, TurnaroundCols.stage3_score].quantile(1.0 - self.pass_percentile)

            # 컷오프 미달 탈락 처리
            is_below_cutoff = valid_mask & (df[TurnaroundCols.stage3_score] < cutoff_val)
            df.loc[is_below_cutoff, 'fail_reason'] = FailReason.COMPOSITE_SCORE_BELOW_CUTOFF.value

        # 현금흐름 구제: 컷오프 미달로 탈락 판정된 종목 중, 매출성장+건전한 OCF가 확인된
        # 종목(CASH_FLOW_QUALITY_RESCUE 태그)만 되살린다. 점수 자체는 그대로 두어(NaN 처리 안 함)
        # 어떤 근거로 컷오프에 못 미쳤는지 투명하게 남긴다.
        cash_flow_rescued = df[TurnaroundCols.na_reasons].str.contains('CASH_FLOW_QUALITY_RESCUE', na=False)
        is_rescued = cash_flow_rescued & (df['fail_reason'] == FailReason.COMPOSITE_SCORE_BELOW_CUTOFF.value)
        df.loc[is_rescued, 'fail_reason'] = None

        # Status 로깅을 위한 변수 할당
        passed_count = df['fail_reason'].isnull().sum()
        if is_rescued.any():
            self.logger.info(f"[Stage 3] 현금흐름 구제로 {int(is_rescued.sum())}개 종목 추가 통과")
        self.logger.info(f"[Stage 3] {len(df)}개 종목 중 {passed_count}개 턴어라운드 종목 통과 (상위 {self.pass_percentile*100}%)")
        
        # ---------------------------------------------------------
        # 4. 스키마 무결성 검증 (출하 전 최종 확인)
        # ---------------------------------------------------------
        validate_schema(df, TurnaroundMetrics)
        
        return df