import sys
import argparse
import yaml
import pandas as pd
import time
from pykrx import stock
from datetime import date
from pathlib import Path

# 프로젝트 루트 경로 설정 (imports 에러 방지)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from data.loader import QuantDataLoader
from core.pipeline import QuantPipeline
from backtest.forward_return import BacktestEngine

def main():
    # 1. 실행 파라미터 (명령줄 인자) 설정
    parser = argparse.ArgumentParser(description="Stock Screener Backtest Entry Point")
    parser.add_argument("--start", type=int, default=2019, help="백테스트 시작 연도 (기본: 2019)")
    parser.add_argument("--end", type=int, default=2025, help="백테스트 종료 연도 (기본: 2025)")
    args = parser.parse_args()

    print("==================================================")
    print(f"🚀 백테스트 파이프라인 가동: {args.start}년 ~ {args.end}년")
    print("==================================================\n")

    # 2. 환경 설정 로드
    config_path = PROJECT_ROOT / "config" / "params.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            params = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
        return

    # 3. 로더 초기화 및 [핵심] 영업일 캘린더 요청
    loader = QuantDataLoader(use_cache=True)
    
    # 🔥 캘린더 판정 권한을 loader에 전적으로 위임!
    base_dates = loader.get_quarterly_rebalance_dates(args.start, args.end)
    print(f"📅 생성된 리밸런싱 기준일: 총 {len(base_dates)}개 분기")

    # 4. 파이프라인 및 엔진 인스턴스 초기화 (조립)
    pipeline = QuantPipeline(params, loader)
    engine = BacktestEngine(pipeline, loader)

    # 5. 순수 엔진 실행
    print("⏳ 엔진에 날짜 리스트를 주입하고 시뮬레이션을 시작합니다...\n")
    perf_df, port_df = engine.run(base_dates)

    # 6. 결과 산출물 저장
    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    
    perf_path = out_dir / "performance_log.csv"
    port_path = out_dir / "portfolio_log.csv"
    
    perf_df.to_csv(perf_path, index=False)
    port_df.to_csv(port_path, index=False)

    print("\n==================================================")
    print(f"✅ 백테스트 완료! 산출물이 다음 위치에 저장되었습니다:")
    print(f"   - 수익률 로그: {perf_path}")
    print(f"   - 포트폴리오 로그: {port_path}")
    print("==================================================")

if __name__ == "__main__":
    main()