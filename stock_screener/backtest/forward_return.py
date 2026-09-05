import pandas as pd
import numpy as np
from datetime import date, timedelta
import logging

class BacktestEngine:
    def __init__(self, pipeline, loader, fee_rate: float = 0.00015, slippage: float = 0.002,
                 min_portfolio_size: int = 5):
        self.pipeline = pipeline
        self.loader = loader
        # 자산 관련 변수(initial_capital) 제거
        self.fee_rate = fee_rate
        self.slippage = slippage
        # 통과 종목이 이 수 미만이면 집중 리스크를 피하기 위해 그 분기는 거래를 스킵(현금 처리)한다.
        self.min_portfolio_size = min_portfolio_size
        self.logger = logging.getLogger(__name__)

        self.portfolio_log = []
        self.performance_log = []

    def _get_period_return(self, ticker: str, start_date: date, end_date: date) -> float:
        # T+1 진입 로직 (기존과 동일하게 유지)
        df = self.loader.get_historical_ohlcv(ticker, start_date, end_date + timedelta(days=7))
        
        if df is None or df.empty:
            return float('nan')
            
        entry_candidates = df[df.index.date > start_date]
        if entry_candidates.empty:
            return float('nan')
            
        entry_price = entry_candidates.iloc[0]['Close']
        
        exit_candidates = df[df.index.date <= end_date]
        if exit_candidates.empty:
            return float('nan')
            
        exit_price = exit_candidates.iloc[-1]['Close']
        
        if entry_price == 0 or pd.isna(entry_price) or pd.isna(exit_price):
            return float('nan')

        if ticker != 'KS11': 
            effective_buy = entry_price * (1 + self.slippage) * (1 + self.fee_rate)
            effective_sell = exit_price * (1 - self.slippage) * (1 - self.fee_rate)
        else:
            effective_buy = entry_price
            effective_sell = exit_price

        return (effective_sell / effective_buy) - 1

    def _is_delisted(self, ticker: str, as_of_date: date) -> bool:
        """
        as_of_date 기준 KOSPI 유니버스에 ticker가 더 이상 존재하지 않는지 확인합니다.
        _get_period_return이 NaN을 반환했을 때, "실제 상장폐지/거래정지"와 "일시적 데이터 누락"을
        구분하기 위한 용도 — 전자는 생존편향 방지를 위해 손실로 반영해야 하고, 후자는 무리하게
        손실 처리하면 오히려 왜곡이므로 제외하는 편이 안전합니다.
        """
        try:
            universe_df = self.loader.get_kospi_universe(as_of_date)
        except Exception as e:
            self.logger.error(f"[상장폐지 확인 실패] {ticker} 검증 중 유니버스 조회 오류: {e}")
            return False  # 확인 자체가 불가하면 보수적으로 "미확인 데이터 누락"으로 취급 (기존 동작 유지)
        return ticker not in set(universe_df['ticker'])

    def run(self, base_dates: list[date]):
        if len(base_dates) < 2:
            raise ValueError("백테스트를 위해서는 최소 2개 이상의 base_date가 필요합니다.")
        
        self.logger.info(f"🚀 시그널 검증 백테스트 시작: {base_dates[0]} ~ {base_dates[-1]}")

        # 외부에서 주입받은 base_dates를 그대로 순회
        for i in range(len(base_dates) - 1):
            t_date = base_dates[i]
            t_next_date = base_dates[i+1]
            
            self.logger.info(f"🔄 스크리닝 시점 [ {t_date} ]")
            
            final_df, _ = self.pipeline.run(t_date)
            bm_return = self._get_period_return('KS11', t_date, t_next_date)

            # 통과 종목이 아예 없거나 최소 분산 기준(min_portfolio_size) 미달이면 그 분기는
            # 거래를 스킵하고 현금(수익률 0%)으로 처리한다. 소수 종목 집중 베팅이 분기마다
            # 리스크 수준을 들쭉날쭉하게 만들어 Sharpe/MDD 해석을 왜곡하는 문제를 막기 위함 —
            # "통과 후보 자체가 부족하다"를 지수 부진 시 현금 비중을 높이는 것과 같은 방어 신호로 취급.
            if len(final_df) < self.min_portfolio_size:
                if final_df.empty:
                    self.logger.warning("조건 만족 종목 없음.")
                else:
                    self.logger.warning(
                        f"통과 종목 {len(final_df)}개 (최소 기준 {self.min_portfolio_size}개 미달) — "
                        f"집중 리스크 회피를 위해 이번 분기는 거래를 스킵하고 현금으로 처리합니다."
                    )
                self.portfolio_log.append({
                    'date': t_date,
                    'tickers': final_df['ticker'].tolist() if not final_df.empty else [],
                    'num_stocks': len(final_df),
                    'skipped_low_count': True
                })
                self.performance_log.append({
                    'date': t_next_date,
                    'portfolio_return': 0.0,
                    'benchmark_return': bm_return,
                    'excess_return': 0.0 - (bm_return if pd.notna(bm_return) else 0)
                })
                continue

            passed_tickers = final_df['ticker'].tolist()
            period_returns = []

            for ticker in passed_tickers:
                stock_return = self._get_period_return(ticker, t_date, t_next_date)
                if pd.notna(stock_return):
                    period_returns.append(stock_return)
                elif self._is_delisted(ticker, t_next_date):
                    # 상장폐지로 시세 데이터 자체가 없는 경우. 조용히 빼면 생존편향(최악의 결과를
                    # 낸 종목일수록 평균 계산에서 사라져 성과가 부풀려짐)이 생기므로 전손(-100%)으로
                    # 간주해 포함시킨다. 실제 정리매매 단계에서 일부 회수가 가능할 수 있으나,
                    # 그 가격 데이터조차 없는 상황이라 보수적으로 최악을 가정한다.
                    self.logger.warning(
                        f"[상장폐지 확인] {ticker}: {t_next_date} 기준 KOSPI 유니버스에서 사라짐 — "
                        f"전손(-100%)으로 처리합니다."
                    )
                    period_returns.append(-1.0)
                else:
                    self.logger.warning(
                        f"[데이터 누락] {ticker}: 상장은 유지 중이나 시세 데이터를 가져오지 못함 — "
                        f"포트폴리오 계산에서 제외합니다(데이터 품질 이슈로 추정)."
                    )

            if period_returns:
                portfolio_return = np.mean(period_returns)
                self.portfolio_log.append({
                    'date': t_date,
                    'tickers': passed_tickers,
                    'num_stocks': len(passed_tickers),
                    'skipped_low_count': False
                })
            else:
                # 최소 분산 기준은 통과했지만(min_portfolio_size 이상) 상장폐지/거래정지 등으로
                # 전 종목의 수익률 데이터를 못 가져온 예외 상황. 과거엔 이 분기가 performance_log에
                # 아예 기록되지 않고 조용히 누락돼 시계열에 구멍이 생겼음 — 현금 처리로 통일해 기록.
                self.logger.warning(
                    f"{t_date} 기준 통과 종목 {len(passed_tickers)}개 전부 수익률 데이터를 "
                    f"가져오지 못해 이번 분기는 현금으로 처리합니다."
                )
                portfolio_return = 0.0
                self.portfolio_log.append({
                    'date': t_date,
                    'tickers': passed_tickers,
                    'num_stocks': len(passed_tickers),
                    'skipped_low_count': True
                })

            excess_ret = portfolio_return - bm_return if pd.notna(bm_return) else float('nan')

            # 자본금 추적 없이 순수 수익률 기록만 남김
            self.performance_log.append({
                'date': t_next_date,
                'portfolio_return': portfolio_return,
                'benchmark_return': bm_return,
                'excess_return': excess_ret
            })

            self.logger.info(f"✔️ KOSPI: {bm_return * 100:.2f}% | 전략: {portfolio_return * 100:.2f}% (Alpha: {excess_ret * 100:.2f}%)")

        self.logger.info("✅ 시그널 검증 완료!")
        return pd.DataFrame(self.performance_log), pd.DataFrame(self.portfolio_log)