"""
backtest/stage_pass_analysis.py가 저장한 원자료(outputs/stage_pass_*.csv)를 읽어 파이프라인 각 단계의
기여와 무작위 대조군 대비 위치를 계산한다 (조회 전용, API 호출 없음).

판정 기준(결과를 보기 전에 정해둔 것, 2026-10-04): 최종 통과 종목이 무작위 대조군보다 좋다고 보려면
  (1) 분기 단위 초과수익의 t 통계량이 2 이상이고(분기를 독립 표본으로 취급 — 같은 분기 종목끼리는
      시장 국면을 공유하므로 종목 단위 t검정은 낙관적이다),
  (2) 무작위 대조군 분포에서의 평균 백분위가 60% 이상이며,
  (3) 2016~2021과 2022~2025 두 구간 모두에서 성립해야 한다.
이 기준을 못 넘으면 "알파가 입증되지 않았다"로 읽는다(효과가 없다는 증명은 아님 — 표본이 작음).

사용 예:
    quant_env\\Scripts\\python.exe analysis\\stage_pass_stats.py
"""
import sys
import argparse
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

PERIODS = {
    '2016~2018': (2016, 2018),
    '2019~2021': (2019, 2021),
    '2022~2025': (2022, 2025),
    '전체': (1900, 2100),
}
MIN_CAP_FOR_AUDIT = 1e11  # 1,000억 (candidate_report 기본값과 동일)


def _quarter_summary(per_quarter: pd.Series) -> dict:
    """분기별 값(예: 통과-탈락 평균 수익률 차이)을 분기 단위로 요약한다 — 분기를 표본 단위로 쓴다."""
    x = per_quarter.dropna()
    if len(x) < 3:
        return {'n_quarters': len(x), 'mean_pp': np.nan, 't': np.nan, 'hit_rate': np.nan}
    t_stat, _ = scipy_stats.ttest_1samp(x, 0.0)
    return {'n_quarters': len(x), 'mean_pp': x.mean() * 100, 't': t_stat, 'hit_rate': float((x > 0).mean())}


def _with_forward(df: pd.DataFrame) -> pd.DataFrame:
    return df[df['forward_return'].notna()]


def stage_contrast(df: pd.DataFrame, stage: int, min_side: int = 3) -> dict:
    """해당 단계에 도달한 종목 중 통과 vs 탈락의 분기별 평균 수익률 차이(통과-탈락)를 요약한다."""
    col = f'pass_s{stage}'
    pool = _with_forward(df[df[col].notna()])
    diffs = {}
    for base_date, g in pool.groupby('base_date'):
        passed = g.loc[g[col] == 1.0, 'forward_return']
        failed = g.loc[g[col] == 0.0, 'forward_return']
        if len(passed) >= min_side and len(failed) >= min_side:
            diffs[base_date] = passed.mean() - failed.mean()
    out = _quarter_summary(pd.Series(diffs, dtype=float))
    out['n_pass'] = int((pool[col] == 1.0).sum())
    out['n_fail'] = int((pool[col] == 0.0).sum())
    return out


def ladder(df: pd.DataFrame, stage: int) -> dict:
    """해당 단계까지 살아남은 종목의 분기별 평균 수익률 - Stage1 유니버스 전체 평균 (누적 초과수익)."""
    col = f'pass_s{stage}'
    pool = _with_forward(df)
    excess = {}
    for base_date, g in pool.groupby('base_date'):
        survivors = g.loc[g[col] == 1.0, 'forward_return']
        if len(survivors) >= 1:
            excess[base_date] = survivors.mean() - g['forward_return'].mean()
    out = _quarter_summary(pd.Series(excess, dtype=float))
    out['n_survivors'] = int((pool[col] == 1.0).sum())
    return out


def random_control(df: pd.DataFrame, actual_col: str, pool_col: Optional[str] = None,
                   n_draws: int = 2000, seed: int = 0) -> dict:
    """
    분기마다 "실제로 고른 종목(actual_col==1.0)"의 평균 수익률을, 같은 분기 후보 풀(pool_col==1.0,
    None이면 전체 유니버스)에서 같은 개수를 무작위로 뽑은 평균 수익률 분포와 비교한다.

    excess = 실제 - 무작위 기대값(= 풀 평균), percentile = 실제가 무작위 분포에서 차지하는 위치(0~1).
    풀 평균과의 차이만으로는 "운이 얼마나 작용하는지"가 안 보이므로 분포 상의 위치를 함께 본다.
    """
    rng = np.random.default_rng(seed)
    pool_all = _with_forward(df)
    excess, percentiles = {}, {}
    for base_date, g in pool_all.groupby('base_date'):
        actual = g.loc[g[actual_col] == 1.0, 'forward_return'].to_numpy()
        pool = g['forward_return'].to_numpy() if pool_col is None else g.loc[g[pool_col] == 1.0, 'forward_return'].to_numpy()
        k = len(actual)
        if k < 1 or len(pool) <= k:
            continue
        draws = rng.permuted(np.tile(np.arange(len(pool)), (n_draws, 1)), axis=1)[:, :k]
        random_means = pool[draws].mean(axis=1)
        actual_mean = actual.mean()
        excess[base_date] = actual_mean - random_means.mean()
        percentiles[base_date] = float((random_means < actual_mean).mean() + 0.5 * (random_means == actual_mean).mean())

    out = _quarter_summary(pd.Series(excess, dtype=float))
    pct = pd.Series(percentiles, dtype=float)
    out['mean_percentile'] = float(pct.mean()) if len(pct) else np.nan
    return out


def ranking_value(df: pd.DataFrame, min_side: int = 1) -> dict:
    """최종 통과 종목 안에서 shortlist(순위+섹터 제약+시총 필터) vs 나머지의 분기별 평균 수익률 차이."""
    if 'shortlist' not in df.columns:
        return {'n_quarters': 0, 'mean_pp': np.nan, 't': np.nan, 'hit_rate': np.nan}
    final = _with_forward(df[df['pass_s5'] == 1.0])
    diffs = {}
    for base_date, g in final.groupby('base_date'):
        top = g.loc[g['shortlist'] == 1.0, 'forward_return']
        rest = g.loc[g['shortlist'] != 1.0, 'forward_return']
        if len(top) >= min_side and len(rest) >= min_side:
            diffs[base_date] = top.mean() - rest.mean()
    return _quarter_summary(pd.Series(diffs, dtype=float))


def missing_forward_rate(df: pd.DataFrame, stage: int) -> float:
    """해당 단계 통과 종목 중 forward_return이 결측(구간 중 상장폐지 등)인 비율 — 생존편향 점검용."""
    sub = df[df[f'pass_s{stage}'] == 1.0]
    return float(sub['forward_return'].isna().mean()) if len(sub) else np.nan


def audit_final_passers(df: pd.DataFrame) -> dict:
    """최종 통과 종목의 근거 품질: 점수 없이 통과한 비율, 소형주 비율, 지표 결측/이상치 비율."""
    final = df[df['pass_s5'] == 1.0]
    n = len(final)
    if n == 0:
        return {'n_final': 0}

    def share(mask: pd.Series) -> float:
        return float(mask.mean())

    out = {'n_final': n, 'quarters_with_final': int(final['base_date'].nunique())}
    if 'stage3_score' in final.columns:
        out['stage3_unscored'] = share(final['stage3_score'].isna())
    if 'stage4_score' in final.columns:
        out['stage4_unscored'] = share(final['stage4_score'].isna())
    out['small_cap'] = share(final['market_cap'] < MIN_CAP_FOR_AUDIT)
    if 'pbr' in final.columns:
        out['pbr_missing'] = share(final['pbr'].isna())
    if 'psr' in final.columns:
        out['psr_over_100'] = share(final['psr'] > 100)
    if 'per' in final.columns:
        out['per_zero'] = share(final['per'] == 0)  # pykrx는 적자 기업의 PER을 0으로 준다
    return out


def _in_period(df: pd.DataFrame, years: tuple) -> pd.DataFrame:
    year = pd.to_datetime(df['base_date']).dt.year
    return df[(year >= years[0]) & (year <= years[1])]


def _fmt(d: dict, keys) -> str:
    parts = []
    for k in keys:
        v = d.get(k)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            parts.append(f"{k}=NA")
        elif isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        else:
            parts.append(f"{k}={v}")
    return "  ".join(parts)


def load_latest(out_dir: Path) -> Optional[pd.DataFrame]:
    files = sorted(f for f in out_dir.glob("stage_pass_*.csv") if '.partial' not in f.name)
    if not files:
        return None
    print(f"📄 사용 파일: {files[-1].name}")
    return pd.read_csv(files[-1], dtype={'ticker': str})


def main():
    parser = argparse.ArgumentParser(description="단계별 통과 vs 탈락 + 무작위 대조군 분석 (읽기 전용)")
    parser.add_argument("--draws", type=int, default=2000, help="무작위 대조군 반복 횟수")
    args = parser.parse_args()

    df = load_latest(PROJECT_ROOT / "outputs")
    if df is None:
        print("❌ outputs/에 stage_pass_*.csv가 없습니다. 먼저 backtest/stage_pass_analysis.py를 실행하세요.")
        return 1

    pd.set_option('display.width', 220)
    print(f"표본: {df['base_date'].nunique()}개 분기, {len(df)}행 (forward_return 결측 {df['forward_return'].isna().mean():.1%})")

    for label, years in PERIODS.items():
        sub = _in_period(df, years)
        if sub.empty:
            continue
        print("\n" + "=" * 70)
        print(f"[{label}] 분기 {sub['base_date'].nunique()}개")
        print("=" * 70)

        print("\n① 단계별 통과 vs 탈락 (분기별 평균 수익률 차이, %p; t는 분기 단위)")
        for s in range(1, 6):
            print(f"  Stage{s}: " + _fmt(stage_contrast(sub, s), ['n_pass', 'n_fail', 'mean_pp', 't', 'hit_rate', 'n_quarters']))

        print("\n② 누적 초과수익 (그 단계까지 생존한 종목 평균 - 유니버스 평균, %p)")
        for s in range(1, 6):
            print(f"  Stage{s}까지: " + _fmt(ladder(sub, s), ['n_survivors', 'mean_pp', 't', 'hit_rate', 'n_quarters'])
                  + f"  결측률={missing_forward_rate(sub, s):.1%}")

        print("\n③ 최종 통과 vs 무작위 대조군 (percentile 평균 0.5=무작위 수준)")
        for pool_label, pool_col in [('전체 유니버스', None), ('Stage1 통과 풀', 'pass_s1'),
                                     ('Stage2 통과 풀', 'pass_s2'), ('Stage4 통과 풀', 'pass_s4')]:
            r = random_control(sub, 'pass_s5', pool_col, n_draws=args.draws)
            print(f"  vs {pool_label}: " + _fmt(r, ['mean_pp', 't', 'mean_percentile', 'hit_rate', 'n_quarters']))

        print("\n④ shortlist(5종목 규칙) 대조")
        for pool_label, pool_col in [('전체 유니버스', None), ('최종 통과 풀', 'pass_s5')]:
            r = random_control(sub, 'shortlist', pool_col, n_draws=args.draws)
            print(f"  vs {pool_label}: " + _fmt(r, ['mean_pp', 't', 'mean_percentile', 'hit_rate', 'n_quarters']))
        print("  shortlist vs 나머지 통과 종목: " + _fmt(ranking_value(sub), ['mean_pp', 't', 'hit_rate', 'n_quarters']))

        print("\n⑤ 최종 통과 종목 감사 (비율)")
        print("  " + _fmt(audit_final_passers(sub), ['n_final', 'quarters_with_final', 'stage3_unscored', 'stage4_unscored',
                                                    'small_cap', 'pbr_missing', 'psr_over_100', 'per_zero']))
    return 0


if __name__ == "__main__":
    sys.exit(main())
