"""
단순 팩터(저PBR·ROE·모멘텀) 기준선 원자료 수집 스크립트 (2026-10-04).

목적: 5단계 파이프라인이 "아무 팩터도 안 쓰는 무작위"뿐 아니라 "쉬운 단일 팩터"보다도 나은지 비교하기 위한
기준선. 파이프라인과 같은 base_date·같은 forward_return(pykrx 구간 등락률, 상장폐지는 -100% 반영 확인됨)을 쓴다.

분기마다 KOSPI 유니버스 전 종목 x 단순 팩터를 모은다(DART 호출 없음, KRX만):
  - pbr, roe(=EPS/BPS): pykrx get_market_fundamental (한 날짜 1회 호출)
  - mom_12_1: 12개월 수익률과 직전 1개월 수익률로 (1+r12)/(1+r1)-1 (get_market_price_change 2회)
저변동성은 종목별 일봉이 필요해 KRX 호출량이 크므로 이번에는 제외했다(필요 시 별도 결정).

수집만 한다(Command). 통계는 analysis/simple_factor_stats.py가 CSV만 읽어 수행한다(Query).
KRX를 분기마다 호출하므로 반드시 단독 순차 실행한다(병렬 금지).

사용 예:
    quant_env\\Scripts\\python.exe backtest\\simple_factor_baseline.py --start 2016 --end 2025
"""
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import yaml
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from backtest.analysis_guard import QuarterErrorGuard, append_partial
from backtest.warning_tag_gate_analysis import get_ticker_forward_return
from data.loader import QuantDataLoader


def _price_change(loader: QuantDataLoader, start, end) -> pd.Series:
    from pykrx import stock
    s, e = loader._get_nearest_past_bday(start), loader._get_nearest_past_bday(end)
    df = loader._fetch_with_retry(
        lambda: stock.get_market_price_change(s, e, market="KOSPI"), label=f"등락률({s}~{e})")
    return (df['등락률'] / 100.0).rename_axis('ticker')


def build_factor_frame(loader: QuantDataLoader, t_date) -> pd.DataFrame:
    """한 분기의 유니버스 x 단순 팩터 표. 실패 시 예외를 그대로 올려 호출부 guard가 처리한다."""
    from pykrx import stock
    universe = loader.get_kospi_universe(t_date)
    frame = universe[['ticker', 'market_cap']].copy()
    frame['base_date'] = t_date

    d_str = loader._get_nearest_past_bday(t_date)
    fund = loader._fetch_with_retry(
        lambda: stock.get_market_fundamental(d_str, market="KOSPI"), label=f"펀더멘털({d_str})")
    fund.index.name = 'ticker'
    # PBR/EPS/BPS가 0이면 pykrx의 "값 없음"(적자·자본잠식 등)이므로 결측으로 둔다.
    pbr = fund['PBR'].where(fund['PBR'] > 0)
    roe = (fund['EPS'] / fund['BPS']).where(fund['BPS'] > 0)
    frame['pbr'] = frame['ticker'].map(pbr)
    frame['roe'] = frame['ticker'].map(roe)

    r12 = _price_change(loader, t_date - relativedelta(months=12), t_date)
    r1 = _price_change(loader, t_date - relativedelta(months=1), t_date)
    mom = (1 + r12) / (1 + r1) - 1
    frame['mom_12_1'] = frame['ticker'].map(mom)
    return frame


def main():
    parser = argparse.ArgumentParser(description="단순 팩터 기준선 원자료 수집 (KRX 순차 호출)")
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--resume", default=None,
                        help="이어받을 .partial.csv 경로(이미 수집된 분기는 건너뛰고 같은 파일에 이어쓴다)")
    args = parser.parse_args()

    with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
        params = yaml.safe_load(f)
    bt = params.get("backtest", {})
    start_year = args.start if args.start is not None else bt.get("start_year", 2019)
    end_year = args.end if args.end is not None else bt.get("end_year", 2025)

    loader = QuantDataLoader(use_cache=True,
                             ttm_denominator=params.get('global', {}).get('ttm_denominator', 'latest_snapshot'))
    base_dates = loader.get_quarterly_rebalance_dates(start_year, end_year)

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    partial_path = Path(args.resume) if args.resume else out_dir / f"simple_factor_{timestamp}.partial.csv"
    chunks = []
    done = set()
    if args.resume:
        prev = pd.read_csv(partial_path)
        prev['base_date'] = pd.to_datetime(prev['base_date']).dt.date
        done = set(prev['base_date'])
        chunks.append(prev)
        print(f"이어받기: 기수집 {len(done)}개 분기 건너뜀 ({partial_path})")
    guard = QuarterErrorGuard(partial_hint=f"그때까지 수집분: {partial_path}")

    n = len(base_dates) - 1
    print(f"단순 팩터 기준선 수집: {start_year}~{end_year} ({n}개 구간)")
    for i in range(n):
        t_date, t_next = base_dates[i], base_dates[i + 1]
        if t_date in done:
            continue
        print(f"[{i + 1}/{n}] {t_date} -> {t_next}")
        try:
            frame = build_factor_frame(loader, t_date)
            fwd = get_ticker_forward_return(loader, frame['ticker'].tolist(), t_date, t_next)
        except Exception as e:
            print(f"  [건너뜀] 에러: {e}")
            guard.fail(t_date, e)
            time.sleep(3.0)
            continue
        guard.ok()
        frame['forward_return'] = frame['ticker'].map(fwd.set_index('ticker')['forward_return'])
        chunks.append(frame)
        append_partial(partial_path, frame)
        time.sleep(1.0)

    if chunks:
        result = pd.concat(chunks, ignore_index=True)
        final_path = out_dir / f"simple_factor_{timestamp}.csv"
        result.to_csv(final_path, index=False, encoding="utf-8-sig")
        print(f"\n저장 완료: {final_path} ({len(result)}행, {result['base_date'].nunique()}개 분기)")
        print("통계는 analysis/simple_factor_stats.py로 확인하세요.")
    else:
        print("\n수집된 데이터가 없습니다.")
    return guard.report(n)


if __name__ == "__main__":
    sys.exit(main())
