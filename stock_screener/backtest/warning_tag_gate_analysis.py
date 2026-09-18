"""
기존에 계산만 되고 필터링엔 안 쓰이는 warning_tags(is_cost_cutting_warning, is_pbr_value_trap,
Stage5의 [ICR미달]/[이익질주의]/[과다부채])를 실제 배제 게이트로 전환할 가치가 있는지 저비용
검증한다.

배경: 2026-09-17 IC 분석 결과 "기존 설계에 정교함을 추가"(가중치/새 팩터)는 계속 역효과였던
반면, Stage1~5의 순차 필터(게이트) 구조 자체는 검증된 성과를 낸다는 게 확인됐다. 그렇다면
"기존 팩터를 더 정교하게" 대신 "거친 게이트를 하나 더" 추가하는 방향이 낫지 않겠냐는 논의에서,
이미 계산되고 있지만 판정에 미반영인 warning_tags를 재활용하는 게 가장 싼 실험이라는 결론.

방법: 각 태그가 실제로 계산되는 stage의 입력 후보군 전체(통과/탈락 무관)에서, 태그가 붙은
그룹과 안 붙은 그룹의 forward return 평균을 비교한다. 태그 그룹이 유의하게 더 나쁘면 게이트로
전환할 근거가 있다는 뜻이다.

사용 예:
    python3 backtest/warning_tag_gate_analysis.py
    python3 backtest/warning_tag_gate_analysis.py --start 2014 --end 2018   # 다른 기간(out-of-sample) 검증
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
from scipy import stats as scipy_stats

from core.schema import Stage
from core.pipeline import QuantPipeline
from data.loader import QuantDataLoader


def get_ticker_forward_return(loader: QuantDataLoader, tickers: list[str], start_date, end_date) -> pd.DataFrame:
    from pykrx import stock
    start_str = loader._get_nearest_past_bday(start_date)
    end_str = loader._get_nearest_past_bday(end_date)
    df = loader._fetch_with_retry(
        lambda: stock.get_market_price_change(start_str, end_str, market="KOSPI"),
        label=f"구간 수익률({start_str}~{end_str})"
    ).reset_index()
    df = df[['티커', '등락률']].rename(columns={'티커': 'ticker', '등락률': 'forward_return'})
    df['forward_return'] = df['forward_return'] / 100.0
    return df[df['ticker'].isin(tickers)]


def main():
    parser = argparse.ArgumentParser(description="기존 warning_tags 게이트 전환 가치 분석")
    parser.add_argument("--start", type=int, default=None, help="시작 연도 (기본: params.yaml backtest.start_year)")
    parser.add_argument("--end", type=int, default=None, help="종료 연도 (기본: params.yaml backtest.end_year)")
    args = parser.parse_args()

    print("=" * 50)
    print("기존 warning_tags 게이트 전환 가치 분석")
    print("=" * 50)

    with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
        params = yaml.safe_load(f)

    backtest_params = params.get("backtest", {})
    start_year = args.start if args.start is not None else backtest_params.get("start_year", 2019)
    end_year = args.end if args.end is not None else backtest_params.get("end_year", 2025)

    ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
    loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)
    pipeline = QuantPipeline(params, loader)
    base_dates = loader.get_quarterly_rebalance_dates(start_year, end_year)

    # (태그가 계산되는 stage, 태그 판정 방식) — 각 태그는 해당 stage의 "입력 후보군 전체"에서 계산됨
    records = {'stage3_cost_cutting': [], 'stage4_pbr_trap': [], 'stage5_icr': [], 'stage5_profit_quality': [], 'stage5_debt': []}

    for i in range(len(base_dates) - 1):
        t_date, t_next = base_dates[i], base_dates[i + 1]
        print(f"[{i + 1}/{len(base_dates) - 1}] {t_date} -> {t_next}")
        try:
            stage1_survivors, _ = pipeline.run(t_date, stop_after=Stage.STAGE1)
            if stage1_survivors.empty:
                continue

            stage2_raw = pipeline.stage2.run(stage1_survivors, loader, t_date)
            stage2_acc = pipeline._accumulate_results(stage1_survivors, stage2_raw)
            stage2_survivors = pipeline._filter_passed(stage2_acc)
            if stage2_survivors.empty:
                continue

            stage3_raw = pipeline.stage3.run(stage2_survivors, loader, t_date)
            stage3_acc = pipeline._accumulate_results(stage2_survivors, stage3_raw)
            stage3_survivors = pipeline._filter_passed(stage3_acc)

            stage4_acc = pd.DataFrame()
            if not stage3_survivors.empty:
                stage4_raw = pipeline.stage4.run(stage3_survivors, loader, t_date)
                stage4_acc = pipeline._accumulate_results(stage3_survivors, stage4_raw)
                stage4_survivors = pipeline._filter_passed(stage4_acc)
            else:
                stage4_survivors = pd.DataFrame()

            stage5_acc = pd.DataFrame()
            if not stage4_survivors.empty:
                stage5_raw = pipeline.stage5.run(stage4_survivors, loader, t_date)
                stage5_acc = pipeline._accumulate_results(stage4_survivors, stage5_raw)
        except Exception as e:
            print(f"  ⚠️ 건너뜀(에러): {e}")
            time.sleep(3.0)
            continue

        # 이번 분기에 등장한 모든 티커의 forward return을 한 번에 조달
        all_tickers = set()
        for acc in [stage3_acc, stage4_acc, stage5_acc]:
            if not acc.empty:
                all_tickers.update(acc['ticker'].tolist())
        if not all_tickers:
            continue
        fwd = get_ticker_forward_return(loader, list(all_tickers), t_date, t_next)
        fwd_map = fwd.set_index('ticker')['forward_return']

        def collect(acc_df, tag_col, is_flagged_fn, key):
            if acc_df.empty or tag_col not in acc_df.columns:
                return
            sub = acc_df[['ticker', tag_col]].copy()
            sub['forward_return'] = sub['ticker'].map(fwd_map)
            sub['flagged'] = sub[tag_col].apply(is_flagged_fn)
            sub = sub.dropna(subset=['forward_return'])
            sub['base_date'] = t_date
            if not sub.empty:
                records[key].append(sub[['ticker', 'flagged', 'forward_return', 'base_date']])

        collect(stage3_acc, 'is_cost_cutting_warning', lambda x: bool(x), 'stage3_cost_cutting')
        collect(stage4_acc, 'is_pbr_value_trap', lambda x: bool(x), 'stage4_pbr_trap')
        collect(stage5_acc, 'warning_tags', lambda x: '[ICR미달]' in str(x), 'stage5_icr')
        collect(stage5_acc, 'warning_tags', lambda x: '[이익질주의]' in str(x), 'stage5_profit_quality')
        collect(stage5_acc, 'warning_tags', lambda x: '[과다부채]' in str(x), 'stage5_debt')

        time.sleep(1.0)

    print("\n" + "=" * 50)
    print("결과: 태그별 flagged vs unflagged forward return 비교")
    print("=" * 50)

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for key, chunks in records.items():
        if not chunks:
            print(f"{key}: 데이터 없음")
            continue
        df = pd.concat(chunks, ignore_index=True)
        flagged = df[df['flagged']]['forward_return']
        unflagged = df[~df['flagged']]['forward_return']
        if len(flagged) < 5 or len(unflagged) < 5:
            print(f"{key}: 표본 부족(flagged={len(flagged)}, unflagged={len(unflagged)})")
            continue
        t_stat, p_val = scipy_stats.ttest_ind(flagged, unflagged, equal_var=False)
        print(f"{key}: flagged n={len(flagged)} 평균={flagged.mean()*100:.2f}% | "
              f"unflagged n={len(unflagged)} 평균={unflagged.mean()*100:.2f}% | "
              f"차이={( flagged.mean()-unflagged.mean())*100:.2f}%p, t={t_stat:.2f}, p={p_val:.4f} "
              f"{'(유의)' if p_val < 0.05 else ''}")
        df.to_csv(out_dir / f"warning_tag_{key}_{timestamp}.csv", index=False, encoding="utf-8-sig")

    print(f"\n원자료 저장 위치: {out_dir}")


if __name__ == "__main__":
    main()
