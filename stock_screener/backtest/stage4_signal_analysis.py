"""
Stage4 밸류에이션 팩터(PBR_inv, BPS_growth, PER_inv) forward-return IC 분석.

2026-09-17 `per_weight` A/B 백테스트에서 PER을 composite score에 추가했더니 성과가 크게
악화됐다(annual, avg_4q에 이은 3번째 "정교화 시도 역효과" 사례). 이 스크립트는 "새로 추가한
팩터 자체의 예측력이 약해서 기존 좋은 신호를 희석시켰다"는 가설을 직접 검증한다 — 27개 분기
각각에서 실제 Stage3 통과 후보군(Stage4의 진짜 입력 모집단)을 구하고, PBR_inv_z/BPS_growth_z/
PER_inv_z 세 팩터 각각과 종목별 forward return의 상관관계(IC)를 따로 측정한다.
stage1_signal_analysis.py와 동일한 방법론(풀링 Spearman + Fama-MacBeth 분기평균)을 쓴다.

사용 예:
    python3 backtest/stage4_signal_analysis.py
"""
import sys
import time
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import yaml
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from core.schema import Stage
from core.pipeline import QuantPipeline
from core.metrics_utils import calc_zscore
from data.loader import QuantDataLoader


def compute_stage4_factors(df: pd.DataFrame, loader: QuantDataLoader, base_date, zscore_clip_lower: float, zscore_clip_upper: float) -> pd.DataFrame:
    """stage4_valuation.py의 팩터 계산 로직을 그대로 재사용(가중합산/컷오프 판정 부분만 제외)."""
    date_1y_ago = base_date - relativedelta(years=1)
    fund_t0 = loader.get_market_fundamental_cross_section(base_date)
    fund_t4 = loader.get_market_fundamental_cross_section(date_1y_ago)
    if fund_t0.empty or fund_t4.empty:
        return pd.DataFrame()

    fund_t0.columns = fund_t0.columns.str.lower()
    fund_t4.columns = fund_t4.columns.str.lower()
    fund_t4 = fund_t4[['ticker', 'bps']].rename(columns={'bps': 'bps_1y_ago'})

    merged = pd.merge(df, fund_t0, on='ticker', how='left')
    merged = pd.merge(merged, fund_t4, on='ticker', how='left')

    merged['bps_growth'] = np.where(
        pd.notna(merged['bps']) & pd.notna(merged['bps_1y_ago']) & (merged['bps_1y_ago'] != 0),
        (merged['bps'] - merged['bps_1y_ago']) / np.abs(merged['bps_1y_ago']),
        np.nan
    )

    safe_pbr = merged['pbr'].apply(lambda x: x if pd.notna(x) and x > 0 else np.nan)
    merged['pbr_inv_z'] = calc_zscore(1 / safe_pbr, zscore_clip_lower, zscore_clip_upper)
    merged['bps_z'] = calc_zscore(merged['bps_growth'], zscore_clip_lower, zscore_clip_upper)

    safe_per = merged['per'].apply(lambda x: x if pd.notna(x) and x > 0 else np.nan) if 'per' in merged.columns else np.nan
    merged['per_inv_z'] = calc_zscore(1 / safe_per, zscore_clip_lower, zscore_clip_upper)

    return merged[['ticker', 'pbr_inv_z', 'bps_z', 'per_inv_z']]


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
    print("=" * 50)
    print("Stage4 팩터 IC 분석 — pbr_inv_z / bps_z / per_inv_z")
    print("=" * 50)

    with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
        params = yaml.safe_load(f)

    backtest_params = params.get("backtest", {})
    start_year = backtest_params.get("start_year", 2019)
    end_year = backtest_params.get("end_year", 2025)
    stage4_params = params.get("stage4_valuation", {})
    zscore_clip_lower = stage4_params.get("zscore_clip_lower", 0.01)
    zscore_clip_upper = stage4_params.get("zscore_clip_upper", 0.99)

    ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
    loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)
    pipeline = QuantPipeline(params, loader)
    base_dates = loader.get_quarterly_rebalance_dates(start_year, end_year)

    records = []
    for i in range(len(base_dates) - 1):
        t_date, t_next = base_dates[i], base_dates[i + 1]
        print(f"[{i + 1}/{len(base_dates) - 1}] {t_date} -> {t_next}")
        try:
            stage3_survivors, _ = pipeline.run(t_date, stop_after=Stage.STAGE3)
            if stage3_survivors.empty:
                print("  ⚠️ Stage3 통과 종목 없음, 건너뜀")
                continue
            factors = compute_stage4_factors(stage3_survivors, loader, t_date, zscore_clip_lower, zscore_clip_upper)
            if factors.empty:
                print("  ⚠️ 밸류에이션 원자료 없음, 건너뜀")
                continue
            fwd = get_ticker_forward_return(loader, factors['ticker'].tolist(), t_date, t_next)
        except Exception as e:
            print(f"  ⚠️ 건너뜀(에러): {e}")
            time.sleep(3.0)
            continue

        merged = pd.merge(factors, fwd, on='ticker', how='inner')
        merged['base_date'] = t_date
        records.append(merged)
        time.sleep(1.0)

    if not records:
        print("❌ 수집된 데이터가 없습니다.")
        return

    all_df = pd.concat(records, ignore_index=True)
    print(f"\n표본 수(분기x종목, Stage3 통과분): {len(all_df)}")

    print("\n=== 팩터별 IC ===")
    for col in ['pbr_inv_z', 'bps_z', 'per_inv_z']:
        sub = all_df.dropna(subset=[col, 'forward_return'])
        pooled_ic = sub[col].corr(sub['forward_return'], method='spearman')

        by_q = sub.groupby('base_date').apply(
            lambda g: g[col].corr(g['forward_return'], method='spearman') if len(g) > 5 else np.nan,
            include_groups=False
        ).dropna()
        mean_ic = by_q.mean()
        se = by_q.std() / np.sqrt(len(by_q)) if len(by_q) > 1 else np.nan
        t_stat = mean_ic / se if se else np.nan

        print(f"{col}: n={len(sub)}, 풀링 IC={pooled_ic:.4f} | 분기평균 IC={mean_ic:.4f}, t-stat={t_stat:.2f} ({len(by_q)}분기)")

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"stage4_ic_analysis_{timestamp}.csv"
    all_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n원자료 저장: {out_path}")


if __name__ == "__main__":
    main()
