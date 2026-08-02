import pandas as pd
import numpy as np
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from pykrx import stock
import logging

class BacktestEngine:
    def __init__(self, pipeline, initial_capital: float = 100000000.0, fee_rate: float = 0.00015, slippage: float = 0.002):
        """
        :param pipeline: 조립이 완료된 QuantPipeline 인스턴스
        :param initial_capital: 초기 투자금 (기본 1억 원)
        :param fee_rate: 거래 수수료 (기본 0.015%)
        :param slippage: 슬리피지 (기본 0.2%)
        """
        self.pipeline = pipeline
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.logger = logging.getLogger(__name__)
        
        # 성과 기록용 리스트
        self.portfolio_log = []
        self.performance_log = []

    def _get_quarterly_rebalance_dates(self, start_date: date, end_date: date) -> list[date]:
        """분기말(3, 6, 9, 12월) 기준의 리밸런싱 영업일 리스트를 생성합니다."""
        dates = []
        curr_date = start_date
        
        while curr_date <= end_date:
            # 해당 월의 마지막 날 계산
            next_month = curr_date.replace(day=28) + timedelta(days=4)
            last_day = next_month - timedelta(days=next_month.day)
            
            # 3, 6, 9, 12월인 경우 리밸런싱 시점으로 판단
            if last_day.month in [3, 6, 9, 12]:
                # 휴일일 경우 pykrx.stock.get_nearest_business_day_in_a_week 등을 활용할 수 있으나,
                # 임시로 해당 일자를 리스트에 추가 (실제 가격 조회 시 가장 가까운 과거 영업일 탐색 처리)
                dates.append(last_day)
                
            curr_date = last_day + timedelta(days=1)
            
        return sorted(list(set(dates)))

    def _get_price(self, ticker: str, target_date: date) -> float:
        """특정 일자의 종가를 가져옵니다. 휴일인 경우 직전 영업일 종가 반환"""
        date_str = target_date.strftime("%Y%m%d")
        # pykrx는 휴일 조회 시 빈 DataFrame을 반환하므로, 최근 7일(영업일 확보) 데이터를 불러와 가장 최근 값을 사용
        past_7_days = (target_date - timedelta(days=7)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv(past_7_days, date_str, ticker)
        
        if df.empty:
            return float('nan')
        return float(df['종가'].iloc[-1])

    def run(self, start_date: date, end_date: date):
        """백테스트 메인 루프"""
        self.logger.info(f"🚀 분기별 백테스트 시작: {start_date} ~ {end_date}")
        rebalance_dates = self._get_quarterly_rebalance_dates(start_date, end_date)
        
        current_capital = self.initial_capital
        
        for i in range(len(rebalance_dates) - 1):
            t_date = rebalance_dates[i]
            t_next_date = rebalance_dates[i+1]
            
            self.logger.info(f"🔄 리밸런싱 [ {t_date} ] (평가금: {current_capital:,.0f}원)")
            
            # 1. 파이프라인 가동 (t 시점 기준 포트폴리오 산출)
            # pipeline.run은 내부적으로 data/loader.py의 point-in-time 공시 시차 검증을 거침
            final_df, _ = self.pipeline.run(t_date)
            
            if final_df.empty:
                self.logger.warning("조건 만족 종목 없음. 현금 100% 보유.")
                self.performance_log.append({
                    'date': t_next_date,
                    'portfolio_return': 0.0,
                    'capital': current_capital
                })
                continue
            
            passed_tickers = final_df['ticker'].tolist()
            weight_per_stock = 1.0 / len(passed_tickers)  # 동일 비중 배분
            
            # 2. t 시점 매수 -> t+1 시점 매도 수익률(Forward Return) 계산
            period_returns = []
            
            for ticker in passed_tickers:
                buy_price = self._get_price(ticker, t_date)
                sell_price = self._get_price(ticker, t_next_date)
                
                if pd.isna(buy_price) or pd.isna(sell_price) or buy_price == 0:
                    continue
                    
                # 슬리피지 및 수수료 반영 실제 수익률
                # 매수 시: 가격이 슬리피지만큼 비싸게 사짐 + 수수료 차감
                effective_buy = buy_price * (1 + self.slippage) * (1 + self.fee_rate)
                # 매도 시: 가격이 슬리피지만큼 싸게 팔림 + 수수료 차감
                effective_sell = sell_price * (1 - self.slippage) * (1 - self.fee_rate)
                
                stock_return = (effective_sell / effective_buy) - 1
                period_returns.append(stock_return)
            
            # 3. 분기 포트폴리오 성과 합산
            if period_returns:
                portfolio_return = np.mean(period_returns) # 동일 비중이므로 산술 평균
                current_capital = current_capital * (1 + portfolio_return)
                
                self.portfolio_log.append({
                    'date': t_date,
                    'tickers': passed_tickers,
                    'num_stocks': len(passed_tickers)
                })
                
                self.performance_log.append({
                    'date': t_next_date,
                    'portfolio_return': portfolio_return,
                    'capital': current_capital
                })
                self.logger.info(f"✔️ 해당 분기 수익률: {portfolio_return * 100:.2f}%")
                
        self.logger.info("✅ 백테스트 완료!")
        return pd.DataFrame(self.performance_log), pd.DataFrame(self.portfolio_log)