"""
Stage 1~5 중 하나를 골라 소수 종목만으로 빠르게 단독 실행해보는 디버깅 도구.

예전 02_strategy_pipeline.ipynb의 stage별 셀들을 대체한다. 그 노트북들은 stage 파라미터를
셀 안에 직접 하드코딩해뒀는데(예: Stage1 pass_ratio 0.4, Stage5는 지금 코드에 있지도 않은
`debt_ratio_percentile_cutoff` 키), 정식 params.yaml이 바뀔 때마다 조용히 낡아버렸다
(2026-09-10 확인). 이 스크립트는 항상 config/params.yaml을 그대로 읽어 그 문제를 원천 차단한다.

사용 예:
    python3 debugging/run_single_stage.py --stage 3 --tickers 005930,000660,105560
    python3 debugging/run_single_stage.py --stage 1 --date 2025-09-30
"""
import sys
import argparse
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import yaml
import numpy as np
import pandas as pd

from data.loader import QuantDataLoader
from stages.stage1_neglected_sector import NeglectedSectorScreener
from stages.stage2_sector_leaders import SectorLeaderScreener
from stages.stage3_fundamental_improve import FundamentalImproveScreener
from stages.stage4_valuation import ValuationScreener
from stages.stage5_financial_health import FinancialHealthScreener

STAGE_CLASSES = {
    2: ('stage2_sector_leaders', SectorLeaderScreener),
    3: ('stage3_fundamental_improve', FundamentalImproveScreener),
    4: ('stage4_valuation', ValuationScreener),
    5: ('stage5_financial_health', FinancialHealthScreener),
}

def build_ticker_df(loader: QuantDataLoader, tickers: list[str], base_date: date, need_roe: bool) -> pd.DataFrame:
    """실제 유니버스에서 sector를 가져와 붙인다(수기 입력 오타 방지). Stage4의 밸류트랩
    태깅이 참고하는 roe도, 필요하면 실제 값을 계산해 함께 넣는다."""
    universe = loader.get_kospi_universe(base_date)
    df = universe[universe['ticker'].isin(tickers)][['ticker', 'sector']].reset_index(drop=True)

    missing = set(tickers) - set(df['ticker'])
    if missing:
        print(f"⚠️ 유니버스에서 찾지 못한 티커(상장폐지/오타 의심): {missing}")

    if need_roe:
        roe_values = []
        for t in df['ticker']:
            ttm = loader.get_ttm_financials(t, base_date)
            roe = np.divide(ttm.get('net_income', np.nan), ttm.get('total_equity', np.nan))
            roe_values.append(roe)
        df['roe'] = roe_values

    return df

def main():
    parser = argparse.ArgumentParser(description="단일 stage 격리 실행 디버깅 도구")
    parser.add_argument("--stage", type=int, required=True, choices=[1, 2, 3, 4, 5], help="실행할 stage 번호")
    parser.add_argument("--tickers", type=str, default="005930,000660,005380,105560",
                         help="쉼표로 구분한 티커 목록 (Stage1은 무시됨 — 섹터 단위 실행)")
    parser.add_argument("--date", type=str, default=None, help="기준일 YYYY-MM-DD (기본: 오늘)")
    args = parser.parse_args()

    base_date = date.fromisoformat(args.date) if args.date else date.today()

    with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
        params = yaml.safe_load(f)

    ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
    loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)

    print("==================================================")
    print(f"🚀 Stage {args.stage} 단독 실행 (기준일 {base_date})")
    print("==================================================\n")

    if args.stage == 1:
        sector_df = loader.get_sector_metrics(base_date)
        screener = NeglectedSectorScreener(params.get('stage1_neglected_sector', {}))
        result = screener.run(sector_df)
        print(f"📊 전체 {len(result)}개 섹터 중 통과: {result['fail_reason'].isnull().sum()}개\n")
        print(result.to_string(index=False))
        return

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    param_key, screener_cls = STAGE_CLASSES[args.stage]
    ticker_df = build_ticker_df(loader, tickers, base_date, need_roe=(args.stage == 4))

    print("📊 입력 종목")
    print(ticker_df.to_string(index=False))
    print()

    screener = screener_cls(params.get(param_key, {}))
    result = screener.run(ticker_df, loader, base_date)

    passed = result['fail_reason'].isnull().sum() if 'fail_reason' in result.columns else len(result)
    print(f"\n✅ {len(result)}개 중 {passed}개 통과")
    print(result.to_string(index=False))

if __name__ == "__main__":
    main()
