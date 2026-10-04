"""
backtest/simple_factor_baseline.py가 저장한 CSV(outputs/simple_factor_*.csv)로 단순 팩터의 성과를 계산한다
(조회 전용, API 호출 없음). 파이프라인 최종 통과(stage_pass_*.csv)와 같은 잣대로 비교할 수 있게 --stage-pass를 주면
같은 분기·같은 시총 필터에서 최종 통과의 초과수익도 함께 출력한다.

지표(분기를 표본 단위로 취급 — 같은 분기 종목끼리는 시장 국면을 공유):
  - IC: 분기별 Spearman(팩터, forward_return)의 평균과 t
  - 상위 20%(최선 쪽) 평균 수익률 - 시총 필터 통과 유니버스 평균, 분기 평균과 t
저PBR은 낮을수록 좋으므로 부호를 뒤집어(-pbr) 다룬다.

사용 예:
    quant_env\\Scripts\\python.exe analysis\\simple_factor_stats.py --file outputs\\simple_factor_XXXX.csv [--stage-pass outputs\\stage_pass_XXXX.csv]
"""
import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

MIN_CAP = 1e11  # 1,000억 (candidate_report 기본값과 동일)
TOP_FRAC = 0.2
MIN_N = 20  # 분기 내 팩터 유효 종목이 이보다 적으면 그 분기는 제외
FACTORS = {'저PBR': ('pbr', -1), 'ROE': ('roe', 1), '모멘텀12-1': ('mom_12_1', 1)}
PERIODS = {'2016~2021': (2016, 2021), '2022~2025': (2022, 2025), '전체': (1900, 2100)}


def _t(x: pd.Series) -> tuple[float, float, int]:
    x = x.dropna()
    if len(x) < 3:
        return np.nan, np.nan, len(x)
    return x.mean(), scipy_stats.ttest_1samp(x, 0.0)[0], len(x)


def factor_quarterly(df: pd.DataFrame, col: str, sign: int) -> pd.DataFrame:
    """분기별 IC와 (상위 20% - 유니버스 평균) 초과수익."""
    rows = {}
    for bd, g in df.groupby('base_date'):
        g = g[g['forward_return'].notna()]
        valid = g[g[col].notna()]
        if len(valid) < MIN_N:
            continue
        score = sign * valid[col]
        ic = scipy_stats.spearmanr(score, valid['forward_return'])[0]
        top = valid[score >= score.quantile(1 - TOP_FRAC)]
        rows[bd] = {'ic': ic, 'excess': top['forward_return'].mean() - g['forward_return'].mean()}
    return pd.DataFrame(rows).T


def final_pass_excess(stage_pass: pd.DataFrame) -> pd.Series:
    """파이프라인 최종 통과(pass_s5==1)의 분기별 (평균 수익률 - 같은 시총 필터 유니버스 평균)."""
    sp = stage_pass[(stage_pass['market_cap'] >= MIN_CAP) & stage_pass['forward_return'].notna()]
    out = {}
    for bd, g in sp.groupby('base_date'):
        fin = g[g['pass_s5'] == 1.0]
        if len(fin) >= 3:
            out[bd] = fin['forward_return'].mean() - g['forward_return'].mean()
    return pd.Series(out, dtype=float)


def _in_period(index, lo, hi):
    years = pd.to_datetime(pd.Index(index)).year
    return (years >= lo) & (years <= hi)


def main():
    parser = argparse.ArgumentParser(description="단순 팩터 기준선 통계 (조회 전용)")
    parser.add_argument("--file", required=True)
    parser.add_argument("--stage-pass", default=None)
    args = parser.parse_args()

    df = pd.read_csv(args.file)
    df['base_date'] = pd.to_datetime(df['base_date'])
    n_q = df['base_date'].nunique()
    print(f"표본: {n_q}개 분기, {len(df)}행 (forward_return 결측 {df['forward_return'].isna().mean():.1%})")
    df = df[df['market_cap'] >= MIN_CAP]
    print(f"시총 {MIN_CAP / 1e8:.0f}억 이상만 사용: {len(df)}행\n")

    results = {name: factor_quarterly(df, col, sign) for name, (col, sign) in FACTORS.items()}
    if args.stage_pass:
        sp = pd.read_csv(args.stage_pass)
        sp['base_date'] = pd.to_datetime(sp['base_date'])
        results['[파이프라인 최종통과]'] = pd.DataFrame({'excess': final_pass_excess(sp)})

    print(f"{'전략':<18}{'구간':<11}{'분기':>5}{'IC평균':>9}{'IC t':>7}{'초과수익%p':>12}{'t':>7}{'승률':>7}")
    for name, q in results.items():
        for label, (lo, hi) in PERIODS.items():
            sub = q[_in_period(q.index, lo, hi)] if len(q) else q
            ex_m, ex_t, n = _t(sub['excess']) if len(sub) else (np.nan, np.nan, 0)
            ic_m, ic_t, _ = _t(sub['ic']) if 'ic' in sub else (np.nan, np.nan, 0)
            win = float((sub['excess'].dropna() > 0).mean()) if n else np.nan
            print(f"{name:<18}{label:<11}{n:>5}{ic_m:>9.3f}{ic_t:>7.2f}{ex_m * 100:>12.2f}{ex_t:>7.2f}{win:>7.0%}")
    print("\n기준: 분기 단위 t≥2 이고 두 구간 모두 성립해야 '효과 있음'으로 읽는다(stage_pass_stats와 동일). "
          "초과수익은 동일가중·T+1/수수료 미반영.")


if __name__ == "__main__":
    sys.exit(main())
