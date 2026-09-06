import sys
import argparse
import yaml
import pandas as pd
import time
from pykrx import stock
from datetime import date, datetime
from pathlib import Path

# 프로젝트 루트 경로 설정 (imports 에러 방지)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from data.loader import QuantDataLoader
from core.pipeline import QuantPipeline
from backtest.forward_return import BacktestEngine

def main():
    # 1. 실행 파라미터 (명령줄 인자) 설정 — 미지정 시 params.yaml의 backtest.start_year/end_year를 사용
    parser = argparse.ArgumentParser(description="Stock Screener Backtest Entry Point")
    parser.add_argument("--start", type=int, default=None, help="백테스트 시작 연도 (기본: params.yaml backtest.start_year)")
    parser.add_argument("--end", type=int, default=None, help="백테스트 종료 연도 (기본: params.yaml backtest.end_year)")
    args = parser.parse_args()

    # 2. 환경 설정 로드
    config_path = PROJECT_ROOT / "config" / "params.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            params = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
        return

    # CLI 인자가 있으면 우선, 없으면 params.yaml 값 사용 (cache_warmup.py와 동일한 단일 출처)
    backtest_params = params.get('backtest', {})
    start_year = args.start if args.start is not None else backtest_params.get('start_year', 2019)
    end_year = args.end if args.end is not None else backtest_params.get('end_year', 2025)

    print("==================================================")
    print(f"🚀 백테스트 파이프라인 가동: {start_year}년 ~ {end_year}년")
    print("==================================================\n")

    # 3. 로더 초기화 및 [핵심] 영업일 캘린더 요청
    ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
    loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)

    # 🔥 캘린더 판정 권한을 loader에 전적으로 위임!
    base_dates = loader.get_quarterly_rebalance_dates(start_year, end_year)
    print(f"📅 생성된 리밸런싱 기준일: 총 {len(base_dates)}개 분기")

    # 4. 파이프라인 및 엔진 인스턴스 초기화 (조립)
    pipeline = QuantPipeline(params, loader)
    engine = BacktestEngine(
        pipeline, loader,
        fee_rate=backtest_params.get('fee_rate', 0.00015),
        slippage=backtest_params.get('slippage', 0.002),
        min_portfolio_size=backtest_params.get('min_portfolio_size', 5)
    )

    # 5. 순수 엔진 실행
    print("⏳ 엔진에 날짜 리스트를 주입하고 시뮬레이션을 시작합니다...\n")
    perf_df, port_df = engine.run(base_dates)

    # 6. 결과 산출물 저장
    # 실행 시각(날짜+시각)을 파일명에 포함 — 오늘만 해도 크래시/재시도로 같은 날 여러 번 돌렸는데
    # 고정 파일명이면 이전 결과가 조용히 덮어써져 실행 이력이 사라짐. 초 단위까지 넣어 같은 날
    # 여러 번 돌려도 서로 덮어쓰지 않게 한다.
    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    perf_path = out_dir / f"performance_log_{run_timestamp}.csv"
    port_path = out_dir / f"portfolio_log_{run_timestamp}.csv"

    perf_df.to_csv(perf_path, index=False)
    port_df.to_csv(port_path, index=False)

    print("\n==================================================")
    print(f"✅ 백테스트 완료! 산출물이 다음 위치에 저장되었습니다:")
    print(f"   - 수익률 로그: {perf_path}")
    print(f"   - 포트폴리오 로그: {port_path}")
    print("==================================================")

if __name__ == "__main__":
    main()