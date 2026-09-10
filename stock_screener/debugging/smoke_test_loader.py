"""
QuantDataLoader 단위 스모크 테스트.

예전 01_data_loader_test.ipynb를 대체 — 노트북 셀 순서/전역 변수에 의존하던 구조를
CLI 스크립트 하나로 통합했다. 로직은 전부 실제 data/loader.py를 그대로 호출하며,
여기서 별도로 재구현하지 않는다(재구현하면 코드가 바뀔 때마다 조용히 낡는 문제가
03_debugging_and_backtest.ipynb 10번 셀에서 실제로 발생했었음 — 2026-09-10).

사용 예:
    python3 debugging/smoke_test_loader.py
    python3 debugging/smoke_test_loader.py --ticker 000660 --date 2025-09-30
"""
import sys
import argparse
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import pandas as pd
from data.loader import QuantDataLoader

def check(label: str, fn):
    print(f"[{label}]")
    try:
        fn()
        print("  ✅ 성공\n")
    except Exception as e:
        print(f"  ❌ 실패: {e}\n")

def main():
    parser = argparse.ArgumentParser(description="QuantDataLoader 스모크 테스트")
    parser.add_argument("--ticker", type=str, default="005930", help="테스트 종목 (기본: 삼성전자)")
    parser.add_argument("--date", type=str, default=None, help="기준일 YYYY-MM-DD (기본: 오늘)")
    args = parser.parse_args()

    ticker = args.ticker
    base_date = date.fromisoformat(args.date) if args.date else date.today()
    year = base_date.year

    print("==================================================")
    print(f"🚀 QuantDataLoader 스모크 테스트 (기준일 {base_date}, 종목 {ticker})")
    print("==================================================\n")

    loader = QuantDataLoader(use_cache=True)

    def _universe():
        df = loader.get_kospi_universe(base_date)
        print(f"  {len(df)}개 종목 로드")
        print(df.head(3).to_string(index=False))

    def _financials():
        data = loader.parse_standardized_financials(ticker, year, '11011')
        keys = ['revenue', 'total_assets', 'total_liabilities', 'total_equity', 'interest_expense']
        for k in keys:
            v = data.get(k, float('nan'))
            print(f"  {k:>20}: {'NaN' if pd.isna(v) else f'{v:,.0f}'}")

    def _ohlcv():
        start = base_date - timedelta(days=180)
        df = loader.get_historical_ohlcv(ticker, start, base_date)
        if df is None or df.empty:
            raise ValueError("빈 데이터프레임 반환")
        print(f"  {len(df)}일치 로드, 최근 종가: {df['Close'].iloc[-1]:,.0f}")

    def _isolated_diff():
        q1 = loader.get_isolated_quarterly_financials(ticker, year, 1, base_date=base_date)
        q2 = loader.get_isolated_quarterly_financials(ticker, year, 2, base_date=base_date)
        print(f"  1Q(누적=단독) 매출액: {q1.get('revenue', float('nan')):,.0f}")
        print(f"  2Q(차분 적용) 매출액: {q2.get('revenue', float('nan')):,.0f}")

    def _rate_limit_counter():
        print(f"  현재 세션 DART 호출 수: {loader.dart_call_count}회 (일일 한도 {loader.dart_daily_limit}회)")

    def _disclosure_lag():
        # 최근 완결된 분기의 발표 시한 전/후를 대략적인 오프셋(분기말+30일 vs +75일)으로 근사한다.
        # 정확한 실제 발표일은 종목마다 다르므로 이건 엄밀한 pass/fail 단정이 아니라 참고용 출력.
        end_month = ((base_date.month - 1) // 3) * 3 + 3  # 분기 마지막 달 (3/6/9/12)
        q_end = (date(base_date.year, end_month, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if q_end >= base_date:
            q_end = date(base_date.year - 1, 12, 31)
        before = q_end + timedelta(days=30)
        after = q_end + timedelta(days=75)
        quarter = (q_end.month - 1) // 3 + 1

        data_before = loader.get_isolated_quarterly_financials(ticker, q_end.year, quarter, base_date=before)
        data_after = loader.get_isolated_quarterly_financials(ticker, q_end.year, quarter, base_date=after)
        rev_before = data_before.get('revenue', float('nan'))
        rev_after = data_after.get('revenue', float('nan'))
        before_str = 'NaN(미공시로 정상 차단)' if pd.isna(rev_before) else f'{rev_before:,.0f}'
        after_str = 'NaN(아직도 미공시 — 발표 지연 가능성)' if pd.isna(rev_after) else f'{rev_after:,.0f}'
        print(f"  분기말 {q_end} + 30일({before}) 시점 매출액: {before_str}")
        print(f"  분기말 {q_end} + 75일({after}) 시점 매출액: {after_str}")

    check("검증 1: KOSPI 유니버스 point-in-time 로드", _universe)
    check("검증 2: DART 재무제표 파싱 (자산/부채/자본/이자비용 포함)", _financials)
    check("검증 3: 시계열 OHLCV 로드", _ohlcv)
    check("검증 4: 분기 단독값 차분(Isolation) 로직", _isolated_diff)
    check("검증 5: DART 호출 카운터", _rate_limit_counter)
    check("검증 6: 공시 시차(Disclosure Lag) 근사 확인", _disclosure_lag)

    print("==================================================")
    print("🎯 스모크 테스트 종료")
    print("==================================================")

if __name__ == "__main__":
    main()
