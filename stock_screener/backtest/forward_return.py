import pandas as pd
import numpy as np
from datetime import date, timedelta
import logging

class BacktestEngine:
    def __init__(self, pipeline, loader, fee_rate: float = 0.00015, slippage: float = 0.002):
        self.pipeline = pipeline
        self.loader = loader
        # 자산 관련 변수(initial_capital) 제거
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.logger = logging.getLogger(__name__)
        
        self.portfolio_log = []
        self.performance_log = []

    def _get_quarterly_rebalance_dates(self, start_date: date, end_date: date) -> list[date]:
        dates = []
        curr_date = start_date
        while curr_date <= end_date:
            next_month = curr_date.replace(day=28) + timedelta(days=4)
            last_day = next_month - timedelta(days=next_month.day)
            
            if last_day.month in [3, 6, 9, 12]:
                dates.append(last_day)
            curr_date = last_day + timedelta(days=1)
        return sorted(list(set(dates)))

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

    def run(self, start_date: date, end_date: date):
        self.logger.info(f"🚀 시그널 검증 백테스트 시작: {start_date} ~ {end_date}")
        rebalance_dates = self._get_quarterly_rebalance_dates(start_date, end_date)
        
        for i in range(len(rebalance_dates) - 1):
            t_date = rebalance_dates[i]
            t_next_date = rebalance_dates[i+1]
            
            self.logger.info(f"🔄 스크리닝 시점 [ {t_date} ]")
            
            final_df, _ = self.pipeline.run(t_date)
            bm_return = self._get_period_return('KS11', t_date, t_next_date)
            
            if final_df.empty:
                self.logger.warning("조건 만족 종목 없음.")
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
            
            if period_returns:
                portfolio_return = np.mean(period_returns)
                
                self.portfolio_log.append({
                    'date': t_date,
                    'tickers': passed_tickers,
                    'num_stocks': len(passed_tickers)
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