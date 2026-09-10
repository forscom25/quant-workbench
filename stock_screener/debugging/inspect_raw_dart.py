"""
DART 원본 재무제표 계정 테이블을 그대로 덤프해서, 계정 매핑(ACCOUNT_MAPPING)이 왜
특정 값을 못 찾는지 눈으로 확인하는 도구.

예전 01_data_loader_test.ipynb 5번 셀, 03_debugging_and_backtest.ipynb의
scan_invalid_tickers_accounts()를 대체 — 여러 종목을 한 번에 훑던 하드코딩 리스트 대신
CLI로 종목/연도/보고서를 지정하고, 여러 개면 쉼표로 이어 넘기면 된다.

사용 예:
    python3 debugging/inspect_raw_dart.py --ticker 002310 --year 2023
    python3 debugging/inspect_raw_dart.py --ticker 002310,069620 --year 2023 --report 11011 --fs-div CFS
"""
import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import pandas as pd
from data.loader import QuantDataLoader

def main():
    parser = argparse.ArgumentParser(description="DART 원본 계정 테이블 덤프 도구")
    parser.add_argument("--ticker", type=str, required=True, help="쉼표로 구분한 티커 목록")
    parser.add_argument("--year", type=int, required=True, help="사업연도")
    parser.add_argument("--report", type=str, default="11011",
                         help="보고서 코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
    parser.add_argument("--fs-div", type=str, default="CFS", choices=["CFS", "OFS"],
                         help="CFS=연결재무제표, OFS=개별재무제표")
    parser.add_argument("--section", type=str, default=None,
                         help="sj_div 필터 (예: IS, BS, CF). 기본은 전체")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.ticker.split(",") if t.strip()]
    loader = QuantDataLoader(use_cache=True)
    pd.set_option('display.max_rows', None)

    for ticker in tickers:
        print("=" * 60)
        print(f"🔍 [{ticker}] {args.year}년 {args.report} ({args.fs_div}) 원본 계정")
        print("=" * 60)

        df = loader.get_financial_statements(ticker, args.year, args.report, fs_div=args.fs_div)
        if df is None or df.empty:
            print("캐시된 데이터가 없거나 로드에 실패했습니다 (해당 조합에 공시 자체가 없을 수 있음).\n")
            continue

        if args.section:
            df = df[df['sj_div'] == args.section]
            if df.empty:
                print(f"'{args.section}' 섹션 데이터가 없습니다.\n")
                continue

        cols = [c for c in ['sj_div', 'account_nm', 'account_id', 'thstrm_amount'] if c in df.columns]
        print(df[cols].fillna('NaN').to_string(index=False))
        print()

if __name__ == "__main__":
    main()
