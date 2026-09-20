"""
신호 분석 3종(stage1/stage4_signal_analysis, warning_tag_gate_analysis)의 원자료(outputs/*.csv)를 구간별로 재집계한다.

각 스크립트는 전체 기간을 한 번만 돌려 원자료(base_date 포함)를 저장하므로, 여기서 base_date 기준으로 구간을 나눠
"신호가 국면과 무관하게 같은 방향인가(안정성)"와 "전체 풀링 시 검정력"을 함께 본다(2026-09-20 도입). 파일을 읽기만 하는
순수 조회(Query) 스크립트이고 KRX/DART를 호출하지 않는다.

통계 공식은 각 분석 스크립트의 출력부와 동일해야 한다(전체 구간 행이 스크립트가 직접 출력한 값과 일치하는지로 검증):
  - stage1/stage4: 풀링 Spearman IC + 분기별 Spearman IC의 평균/t-stat (Fama-MacBeth)
  - warning_tag: flagged vs unflagged 평균 forward return 차이, Welch t-test

사용 예:
    python3 analysis/signal_period_breakdown.py                       # outputs/의 최신 최종 CSV 사용
    python3 analysis/signal_period_breakdown.py --stage1 outputs/stage1_ic_analysis_XXXX.csv
"""
import argparse
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "outputs"

# (라벨, 시작일, 종료일) — 2016~2018은 분기 재무 데이터가 갖춰진 첫 구간이자 2019~2025 결론 도출 이후의 out-of-sample
WINDOWS = [("2016-2018(OOS)", "2016-01-01", "2018-12-31"),
           ("2019-2021", "2019-01-01", "2021-12-31"),
           ("2022-2025", "2022-01-01", "2025-12-31"),
           ("ALL 2016-2025", "2016-01-01", "2025-12-31")]


def latest(pattern):
    """가장 최근 최종 CSV(이어쓰기 중간 저장본 *.partial.csv 제외)."""
    fs = [f for f in glob.glob(str(OUT / pattern)) if not f.endswith(".partial.csv")]
    fs.sort(key=os.path.getmtime)
    return fs[-1] if fs else None


def window(df, a, b):
    d = pd.to_datetime(df["base_date"])
    return df[(d >= a) & (d <= b)]


def ic_rows(df, cols, title):
    print(f"\n### {title}")
    print(f"{'구간':<18}{'팩터':<16}{'n':>6}{'분기수':>6}{'풀링IC':>9}{'분기평균IC':>11}{'t-stat':>8}")
    for name, a, b in WINDOWS:
        w = window(df, a, b)
        for c in cols:
            sub = w.dropna(subset=[c, "forward_return"])
            if len(sub) < 10:
                print(f"{name:<18}{c:<16}{len(sub):>6}  표본 부족")
                continue
            pooled = sub[c].corr(sub["forward_return"], method="spearman")
            by_q = sub.groupby("base_date").apply(
                lambda g: g[c].corr(g["forward_return"], method="spearman") if len(g) > 5 else np.nan,
                include_groups=False).dropna()
            m = by_q.mean()
            se = by_q.std() / np.sqrt(len(by_q)) if len(by_q) > 1 else np.nan
            t = m / se if se else np.nan
            print(f"{name:<18}{c:<16}{len(sub):>6}{len(by_q):>6}{pooled:>9.4f}{m:>11.4f}{t:>8.2f}")


def gate_rows():
    print("\n### warning_tags 게이트 (flagged - unflagged forward return, Welch t)")
    print(f"{'태그':<24}{'구간':<18}{'flag n':>7}{'unfl n':>8}{'flag평균':>9}{'unfl평균':>9}{'차이%p':>8}{'t':>7}{'p':>8}")
    for key in ["stage3_cost_cutting", "stage4_pbr_trap", "stage5_icr", "stage5_profit_quality", "stage5_debt"]:
        f = latest(f"warning_tag_{key}_*.csv")
        if not f:
            print(f"{key:<24}데이터 없음")
            continue
        df = pd.read_csv(f)
        for name, a, b in WINDOWS:
            w = window(df, a, b)
            fl = w[w["flagged"]]["forward_return"]
            un = w[~w["flagged"]]["forward_return"]
            if len(fl) < 5 or len(un) < 5:
                print(f"{key:<24}{name:<18}{len(fl):>7}{len(un):>8}  표본 부족")
                continue
            t, p = st.ttest_ind(fl, un, equal_var=False)
            print(f"{key:<24}{name:<18}{len(fl):>7}{len(un):>8}{fl.mean()*100:>8.2f}%{un.mean()*100:>8.2f}%"
                  f"{(fl.mean()-un.mean())*100:>8.2f}{t:>7.2f}{p:>8.4f}{' *' if p < 0.05 else ''}")


def main():
    parser = argparse.ArgumentParser(description="신호 분석 원자료 구간별 재집계")
    parser.add_argument("--stage1", default=None, help="stage1_ic_analysis CSV 경로 (기본: outputs/의 최신 최종본)")
    parser.add_argument("--stage4", default=None, help="stage4_ic_analysis CSV 경로 (기본: outputs/의 최신 최종본)")
    args = parser.parse_args()

    f1 = args.stage1 or latest("stage1_ic_analysis_*.csv")
    f4 = args.stage4 or latest("stage4_ic_analysis_*.csv")
    print("입력:", f1, f4, sep="\n  ")
    if f1:
        ic_rows(pd.read_csv(f1), ["return_z_score", "volume_z_score"], "Stage1 (분기x섹터)")
    if f4:
        ic_rows(pd.read_csv(f4), ["pbr_inv_z", "bps_z", "per_inv_z"], "Stage4 (분기x종목, Stage3 통과분)")
    gate_rows()


if __name__ == "__main__":
    main()
