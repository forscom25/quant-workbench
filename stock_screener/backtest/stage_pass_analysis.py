"""
단계별 통과 vs 탈락 종목의 다음 분기 수익률을 비교하기 위한 원자료 수집 스크립트 (2026-10-04).

배경: 지금까지의 신호 분석(stage1/stage4_signal_analysis, warning_tag_gate_analysis)은 "개별 신호가
수익률과 상관이 있나"만 쟀고, 파이프라인의 각 단계 필터가 실제로 좋은 종목을 남기는지(통과 vs 탈락)는
직접 재지 못했다. 파이프라인 전체 초과수익도 0과 구분되지 않아(2026-09-21), 단계별 기여와 무작위 대조군
비교가 필요하다.

수집만 한다(Command). 통계/해석은 analysis/stage_pass_stats.py가 저장된 CSV만 읽어 수행한다(Query).

분기마다 파이프라인을 전체 실행해 history에서 아래를 모은다:
  - Stage1 유니버스 전 종목 x 단계별 통과 여부(pass_s1~s5: 1.0=통과, 0.0=탈락, NaN=그 단계에 도달 못 함)
  - 단계 점수, 몇 가지 핵심 지표(감사용), 후보 리포트 기준 rank_score/shortlist
  - t_date -> 다음 리밸런싱일 구간 수익률(forward_return)

[알려진 한계] forward_return은 pykrx 구간 등락률(종가 대비 종가, T+1 진입·수수료 미반영)이라
warning_tag_gate_analysis와 같은 방식이다. 구간 도중 상장폐지된 종목은 등락률이 없어 NaN이 되므로(백테스트
엔진은 -100%로 반영) 탈락 쪽 평균이 낙관적으로 나올 수 있다 — 그룹별 결측률을 분석 단계에서 함께 출력한다.

KRX를 분기마다 호출하므로 반드시 단독 순차 실행한다(병렬 금지, 2026-09-17/20 IP 차단 사고).

사용 예:
    quant_env\\Scripts\\python.exe backtest\\stage_pass_analysis.py --start 2016 --end 2025
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

from core.pipeline import QuantPipeline
from backtest.analysis_guard import QuarterErrorGuard, append_partial
from backtest.warning_tag_gate_analysis import get_ticker_forward_return
from analysis.candidate_report import build_candidate_report
from data.loader import QuantDataLoader

STAGES = ['stage1', 'stage2', 'stage3', 'stage4', 'stage5']
# 점수와 감사용 지표. 단계 history에 있는 컬럼만 가져온다.
CARRY_COLS = ['stage3_score', 'stage4_score', 'stage5_score',
              'roe', 'pbr', 'per', 'psr', 'interest_coverage_ratio', 'debt_ratio']


def build_quarter_frame(history: dict, base_date) -> pd.DataFrame:
    """한 분기의 history를 "Stage1 유니버스 전 종목 x 통과 여부/점수" 표로 바꾼다 (순수 함수)."""
    stage1 = history.get('stage1')
    if stage1 is None or stage1.empty:
        return pd.DataFrame()

    frame = stage1[['ticker', 'sector', 'market_cap']].copy()
    frame['base_date'] = base_date

    for i, name in enumerate(STAGES, start=1):
        df = history.get(name)
        if df is None or df.empty:
            frame[f'pass_s{i}'] = np.nan  # 그 단계에 도달한 종목이 없음
            continue
        by_ticker = df.drop_duplicates('ticker').set_index('ticker')
        frame[f'pass_s{i}'] = frame['ticker'].map(by_ticker['fail_reason'].isnull().astype(float))
        for col in CARRY_COLS:
            # 앞 단계 값이 이후 단계 history에 누적되어 있으므로 처음 등장한 단계에서 한 번만 가져온다.
            if col in by_ticker.columns and col not in frame.columns:
                frame[col] = frame['ticker'].map(by_ticker[col])

    candidates = build_candidate_report(history)
    if not candidates.empty:
        by_ticker = candidates.set_index('ticker')
        frame['rank_score'] = frame['ticker'].map(by_ticker['rank_score'])
        frame['shortlist'] = frame['ticker'].map(by_ticker['shortlist'].astype(float))
    return frame


def main():
    parser = argparse.ArgumentParser(description="단계별 통과 vs 탈락 원자료 수집 (KRX 순차 호출)")
    parser.add_argument("--start", type=int, default=None, help="시작 연도 (기본: params.yaml backtest.start_year)")
    parser.add_argument("--end", type=int, default=None, help="종료 연도 (기본: params.yaml backtest.end_year)")
    args = parser.parse_args()

    with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
        params = yaml.safe_load(f)
    backtest_params = params.get("backtest", {})
    start_year = args.start if args.start is not None else backtest_params.get("start_year", 2019)
    end_year = args.end if args.end is not None else backtest_params.get("end_year", 2025)

    ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
    loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)
    pipeline = QuantPipeline(params, loader)
    base_dates = loader.get_quarterly_rebalance_dates(start_year, end_year)

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    partial_path = out_dir / f"stage_pass_{timestamp}.partial.csv"
    guard = QuarterErrorGuard(partial_hint=f"그때까지 수집분: {partial_path}")

    print("=" * 50)
    print(f"단계별 통과 vs 탈락 원자료 수집: {start_year}~{end_year} ({len(base_dates) - 1}개 구간)")
    print("=" * 50)

    chunks = []
    for i in range(len(base_dates) - 1):
        t_date, t_next = base_dates[i], base_dates[i + 1]
        print(f"[{i + 1}/{len(base_dates) - 1}] {t_date} -> {t_next}")
        try:
            _, history = pipeline.run(t_date)
            frame = build_quarter_frame(history, t_date)
            if frame.empty:
                guard.ok()  # 해당 분기에 유니버스가 비는 정상적인 경우
                continue
            fwd = get_ticker_forward_return(loader, frame['ticker'].tolist(), t_date, t_next)
        except Exception as e:
            print(f"  ⚠️ 건너뜀(에러): {e}")
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
        final_path = out_dir / f"stage_pass_{timestamp}.csv"
        result.to_csv(final_path, index=False, encoding="utf-8-sig")
        print(f"\n저장 완료: {final_path} ({len(result)}행, {result['base_date'].nunique()}개 분기)")
        print("통계는 analysis/stage_pass_stats.py로 확인하세요.")
    else:
        print("\n수집된 데이터가 없습니다.")
    return guard.report(len(base_dates) - 1)


if __name__ == "__main__":
    sys.exit(main())
