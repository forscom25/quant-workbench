"""
Stage1 하드컷→penalty 전환(C안) 착수 전 저비용 사전 검증용 진단 스크립트.

2026-09-08 진단에서 "방산처럼 섹터 모멘텀이 극단적인 곳은 개별 기업 펀더멘털을 전혀 안 보고
Stage1에서 통째로 배제된다"는 문제가 2024~2025년 언더퍼폼의 주요 원인으로 지목됐다. 이 스크립트는
Stage1을 우회해 지정한 티커들을 Stage2~5에 직접 통과시켜, "만약 Stage1이 막지 않았다면 이 종목들이
실제로 후속 단계를 통과했을지"를 역산한다. 통과했다면 penalty 전환(C안) 투자가 실제로 가치 있다는
근거가 되고, 대부분 Stage2~5에서 걸러졌다면 C안의 기대 효과가 작다는 뜻이다.

core/pipeline.py의 _accumulate_results/_filter_passed를 그대로 재사용해 실제 파이프라인과
동일한 병합 로직을 보장한다(드리프트 방지).

사용 예:
    python3 debugging/stage1_bypass_diagnostic.py --tickers 079550,103140,012450,272210,064350 \
        --dates 2024-12-30,2025-03-31,2025-06-30
"""
import sys
import argparse
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import yaml
import pandas as pd

from data.loader import QuantDataLoader
from core.pipeline import QuantPipeline
from core.schema import FailReason


def build_realistic_candidate_pool(pipeline: QuantPipeline, loader: QuantDataLoader, target_tickers: list[str], base_date: date) -> pd.DataFrame:
    """
    실제 Stage1 통과 후보군(수백 개)을 그대로 구하고, 그 안에 target_tickers를 강제로 끼워
    넣는다(이미 통과했으면 중복 추가 안 함). target_tickers만 단독으로 돌리면 Stage3/4의
    Z-score·percentile 컷오프가 5개짜리 소표본 기준으로 계산되어 왜곡되므로(Stage5가 과거
    N=1 문제로 pool 평가로 전환했던 것과 같은 종류의 통계 오류), 반드시 진짜 후보군 규모
    안에서 경쟁시켜야 "Stage1이 안 막았다면 실제로 통과했을지"를 의미 있게 답할 수 있다.
    """
    universe_df = loader.get_kospi_universe(base_date)
    sector_metrics_df = loader.get_sector_metrics(base_date)
    sector_result_df = pipeline.stage1.run(sector_metrics_df)

    sector_extra_cols = sector_result_df.columns.difference(['fail_reason']).tolist()
    sector_status = sector_result_df[sector_extra_cols + ['fail_reason']].rename(
        columns={'fail_reason': 'sector_fail_reason'}
    )
    stage1_ticker_df = pd.merge(universe_df, sector_status, on='sector', how='left')
    stage1_ticker_df['fail_reason'] = None
    stage1_ticker_df.loc[
        stage1_ticker_df['sector_fail_reason'].notna(), 'fail_reason'
    ] = FailReason.SECTOR_NOT_QUALIFIED.value
    stage1_ticker_df.drop(columns=['sector_fail_reason'], inplace=True)

    real_survivors = pipeline._filter_passed(stage1_ticker_df)
    print(f"  실제 Stage1 통과 후보군: {len(real_survivors)}개")

    already_in = set(real_survivors['ticker']) & set(target_tickers)
    if already_in:
        print(f"  (참고) 이미 실제로 Stage1을 통과한 타겟 티커: {already_in}")

    to_inject = universe_df[
        universe_df['ticker'].isin(target_tickers) & ~universe_df['ticker'].isin(real_survivors['ticker'])
    ].copy()
    to_inject['fail_reason'] = None

    missing = set(target_tickers) - set(universe_df['ticker'])
    if missing:
        print(f"  ⚠️ 유니버스에서 찾지 못한 티커(상장폐지/오타 의심): {missing}")

    return pd.concat([real_survivors, to_inject], ignore_index=True)


def trace_tickers(pipeline: QuantPipeline, loader: QuantDataLoader, tickers: list[str], base_date: date) -> pd.DataFrame:
    df = build_realistic_candidate_pool(pipeline, loader, tickers, base_date)
    if df.empty:
        return df

    stage_names = ['stage2', 'stage3', 'stage4', 'stage5']
    stage_objs = [pipeline.stage2, pipeline.stage3, pipeline.stage4, pipeline.stage5]

    target_set = set(tickers)
    rows = []
    current = df
    for name, stage in zip(stage_names, stage_objs):
        if current.empty:
            break
        raw = stage.run(current, loader, base_date)
        # 실제 파이프라인과 동일하게 "진짜 후보군 전체"를 기준으로 병합·정렬(percentile 컷오프
        # 계산에 전체 규모가 그대로 반영됨)하되, 출력은 타겟 티커만 골라서 보여준다.
        accumulated = pipeline._accumulate_results(current, raw)
        for _, row in accumulated[accumulated['ticker'].isin(target_set)].iterrows():
            rows.append({
                'ticker': row['ticker'], 'stage': name,
                'fail_reason': row.get('fail_reason'),
                'score': row.get(f'{name}_score', None),
            })
        current = pipeline._filter_passed(accumulated)

    final_targets = set(current['ticker']) & target_set if not current.empty else set()
    for t in final_targets:
        rows.append({'ticker': t, 'stage': 'FINAL_PASS', 'fail_reason': None, 'score': None})

    trace_df = pd.DataFrame(rows)
    trace_df['base_date'] = base_date
    return trace_df


def main():
    parser = argparse.ArgumentParser(description="Stage1 우회 후 Stage2~5 통과 여부 역산 진단")
    parser.add_argument("--tickers", type=str, required=True, help="쉼표로 구분한 티커 목록")
    parser.add_argument("--dates", type=str, required=True, help="쉼표로 구분한 기준일(YYYY-MM-DD) 목록")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    dates = [date.fromisoformat(d.strip()) for d in args.dates.split(",") if d.strip()]

    with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
        params = yaml.safe_load(f)

    ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
    loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)
    pipeline = QuantPipeline(params, loader)

    all_traces = []
    for d in dates:
        print(f"\n{'=' * 50}\n{d} 기준 Stage1 우회 진단\n{'=' * 50}")
        trace_df = trace_tickers(pipeline, loader, tickers, d)
        if not trace_df.empty:
            print(trace_df.to_string(index=False))
        all_traces.append(trace_df)

    combined = pd.concat(all_traces, ignore_index=True)
    final_passes = combined[combined['stage'] == 'FINAL_PASS']
    print(f"\n{'=' * 50}")
    print(f"최종 통과(Stage2~5 전부 통과): {len(final_passes)}건")
    if not final_passes.empty:
        print(final_passes[['base_date', 'ticker']].to_string(index=False))
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
