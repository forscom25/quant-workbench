import pandas as pd
import numpy as np
from typing import Dict, Union

def calc_cumulative_returns(returns: pd.Series) -> pd.Series:
    """
    단순 수익률(Arithmetic returns) 시계열을 누적 수익률(Cumulative returns) 시계열로 변환합니다.
    """
    return (1 + returns).cumprod() - 1

def calc_cagr(cumulative_return: float, periods: int, periods_per_year: int) -> float:
    """
    연평균 성장률(CAGR)을 계산합니다.
    - periods: 총 운용 기간 (예: 일수, 혹은 분기수)
    - periods_per_year: 연환산 계수 (일간=252, 월간=12, 분기=4)
    """
    if periods == 0:
        return 0.0
    years = periods / periods_per_year
    return (1 + cumulative_return) ** (1 / years) - 1

def calc_mdd(returns: pd.Series) -> tuple[float, pd.Series]:
    """
    최대 낙폭(Maximum Drawdown)과 시계열 낙폭(Drawdown series)을 계산합니다.
    Returns:
        mdd (float): 가장 깊은 낙폭 (음수, 예: -0.25는 -25%)
        drawdown_series (pd.Series): 시점별 낙폭 시계열 (시각화 렌더링용)
    """
    wealth_index = (1 + returns).cumprod()
    previous_peaks = wealth_index.cummax()
    drawdowns = (wealth_index - previous_peaks) / previous_peaks
    mdd = drawdowns.min()
    return mdd, drawdowns

def calc_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02, periods_per_year: int = 252) -> float:
    """
    위험 조정 수익률인 샤프 지수(Sharpe Ratio)를 계산합니다.
    기본 무위험 수익률(Rf)은 2%로 가정합니다.
    """
    if returns.std() == 0:
        return 0.0
    
    # 1기(Period)당 무위험 수익률
    rf_per_period = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
    excess_returns = returns - rf_per_period
    
    # 연율화된 샤프 지수 계산
    annualized_excess_return = excess_returns.mean() * periods_per_year
    annualized_volatility = returns.std() * np.sqrt(periods_per_year)
    
    return annualized_excess_return / annualized_volatility

def calc_win_rate(port_returns: pd.Series, bench_returns: pd.Series) -> float:
    """
    벤치마크 대비 초과 수익이 0보다 큰(이긴) 기간의 비율(승률)을 계산합니다.
    """
    excess_returns = port_returns - bench_returns
    win_periods = (excess_returns > 0).sum()
    total_periods = len(excess_returns)
    
    if total_periods == 0:
        return 0.0
    return win_periods / total_periods

def generate_performance_summary(
    port_returns: pd.Series, 
    bench_returns: pd.Series, 
    periods_per_year: int = 252
) -> Dict[str, Union[float, str]]:
    """
    모든 순수 계산 함수를 조합하여 리포팅용 요약 딕셔너리를 생성합니다.
    (이 결과물을 visualize.py 또는 콘솔 출력 로직에서 넘겨받아 사용합니다)
    """
    port_cum = calc_cumulative_returns(port_returns)
    bench_cum = calc_cumulative_returns(bench_returns)
    
    port_final_cum = port_cum.iloc[-1] if not port_cum.empty else 0.0
    bench_final_cum = bench_cum.iloc[-1] if not bench_cum.empty else 0.0
    
    total_periods = len(port_returns)
    
    port_mdd, _ = calc_mdd(port_returns)
    bench_mdd, _ = calc_mdd(bench_returns)
    
    return {
        "총 운용 기간 (Periods)": total_periods,
        "전략 누적 수익률": port_final_cum,
        "벤치마크 누적 수익률": bench_final_cum,
        "전략 CAGR": calc_cagr(port_final_cum, total_periods, periods_per_year),
        "벤치마크 CAGR": calc_cagr(bench_final_cum, total_periods, periods_per_year),
        "전략 MDD": port_mdd,
        "벤치마크 MDD": bench_mdd,
        "전략 Sharpe Ratio": calc_sharpe_ratio(port_returns, periods_per_year=periods_per_year),
        "초과 승률 (Win Rate)": calc_win_rate(port_returns, bench_returns)
    }