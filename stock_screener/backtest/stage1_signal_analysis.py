"""
Stage1 z_return/z_volume 가중치(w1/w2) 튜닝을 위한 forward-return IC(정보계수) 분석.

w1/w2 조합마다 27분기 풀 백테스트(각 2~4시간)를 반복하는 건 비현실적이라, 대신 Stage1이 실제로
계산하는 return_z_score/volume_z_score 두 신호가 각각 "다음 리밸런싱까지의 섹터 수익률"과 얼마나
상관관계가 있는지(Spearman IC)를 직접 측정한다. DART 호출이 전혀 없고(pykrx 섹터/가격 데이터만
사용) 5단계 전체를 실행하지도 않아 몇 분 내로 끝난다.

사용 예:
    python3 backtest/stage1_signal_analysis.py
    python3 backtest/stage1_signal_analysis.py --start 2014 --end 2018   # 다른 기간(out-of-sample) 검증
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
import pandas as pd

from backtest.analysis_guard import QuarterErrorGuard, append_partial
from data.loader import QuantDataLoader
from stages.stage1_neglected_sector import NeglectedSectorScreener


def main():
    parser = argparse.ArgumentParser(description="Stage1 신호 IC 분석")
    parser.add_argument("--start", type=int, default=None, help="시작 연도 (기본: params.yaml backtest.start_year)")
    parser.add_argument("--end", type=int, default=None, help="종료 연도 (기본: params.yaml backtest.end_year)")
    args = parser.parse_args()

    print("=" * 50)
    print("Stage1 신호 IC(정보계수) 분석 — return_z_score / volume_z_score")
    print("=" * 50)

    with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
        params = yaml.safe_load(f)

    backtest_params = params.get("backtest", {})
    start_year = args.start if args.start is not None else backtest_params.get("start_year", 2019)
    end_year = args.end if args.end is not None else backtest_params.get("end_year", 2025)

    loader = QuantDataLoader(use_cache=True)
    base_dates = loader.get_quarterly_rebalance_dates(start_year, end_year)

    # z-score만 필요하므로 가중치/통과비율은 결과에 영향 없음(전부 통과시켜 모든 섹터의
    # 개별 z-score를 받아온다).
    screener = NeglectedSectorScreener({
        "stage1_return_weight": 0.5,
        "stage1_volume_weight": 0.5,
        "stage1_pass_ratio": 1.0,
    })

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"stage1_ic_analysis_{timestamp}.csv"
    # 2026-09-20: 분기마다 원자료를 .partial.csv에 이어쓰고, 연속 3분기 실패 시 즉시 중단(KRX 차단 시 조용히
    # 부분 표본으로 끝나던 문제 방지 — backtest/analysis_guard.py 참고)
    partial_path = out_dir / f"stage1_ic_analysis_{timestamp}.partial.csv"
    guard = QuarterErrorGuard(partial_hint=f"그때까지 수집분: {partial_path}")

    records = []
    for i in range(len(base_dates) - 1):
        t_date, t_next = base_dates[i], base_dates[i + 1]
        print(f"[{i + 1}/{len(base_dates) - 1}] {t_date} -> {t_next}")
        try:
            sector_df = loader.get_sector_metrics(t_date)
            scored = screener.run(sector_df)
            fwd = loader.get_sector_forward_return(t_date, t_next)
        except Exception as e:
            print(f"  ⚠️ 건너뜀(에러): {e}")
            guard.fail(t_date, e)
            time.sleep(3.0)
            continue
        guard.ok()

        merged = pd.merge(
            scored[["sector", "return_z_score", "volume_z_score"]],
            fwd, on="sector", how="inner"
        )
        merged["base_date"] = t_date
        records.append(merged)
        append_partial(partial_path, merged)

        # 2026-09-17 추가: KRX가 "자동화 수단을 통한 비정상 대량 조회"를 탐지해 IP를 1일간
        # 차단한 사고가 있었음(이 스크립트의 촘촘한 루프 + 그날의 다른 개별 테스트 호출들이
        # 누적된 것으로 추정) — 분기 간 여유를 둬서 짧은 시간에 몰아치는 패턴을 피한다.
        time.sleep(3.0)

    if not records:
        print("❌ 수집된 데이터가 없습니다.")
        return 1

    all_df = pd.concat(records, ignore_index=True)
    all_df = all_df.dropna(subset=["return_z_score", "volume_z_score", "forward_return"])

    ic_return = all_df["return_z_score"].corr(all_df["forward_return"], method="spearman")
    ic_volume = all_df["volume_z_score"].corr(all_df["forward_return"], method="spearman")

    print()
    print("=" * 50)
    print(f"표본 수(분기x섹터, 결측 제외): {len(all_df)}")
    print(f"return_z_score IC (Spearman): {ic_return:.4f}")
    print(f"volume_z_score IC (Spearman): {ic_volume:.4f}")
    print("=" * 50)

    all_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"원자료 저장: {out_path}")
    return guard.report(len(base_dates) - 1)


if __name__ == "__main__":
    sys.exit(main())
