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

def generate_quarterly_dates(start_year: int, end_year: int) -> list[date]:
    """
    지정된 연도 구간의 분기말 날짜를 생성하되, 
    KRX 실제 영업일(해당 월의 마지막 거래일) 기준으로 보정하여 반환합니다.
    (pykrx 캘린더 버그를 우회하기 위해 KOSPI 지수 실제 거래일을 조회합니다)
    """
    start_str = f"{start_year}-01-01"
    end_str = f"{end_year}-12-31"
    
    # 1. 캘린더 기준 분기말 날짜 생성 (주말/휴일 포함)
    quarter_ends = pd.date_range(start=start_str, end=end_str, freq="QE")
    
    valid_b_dates = []
    print("🗓️ KOSPI 실제 거래일 데이터를 바탕으로 분기말 영업일 추출 중...")
    
    for d in quarter_ends:
        month_start = d.replace(day=1).strftime("%Y%m%d")
        month_end = d.strftime("%Y%m%d")
        
        # 🚨 KOSPI("1001") 지수의 시세 데이터를 가져와 인덱스(날짜)만 활용
        try:
            df = stock.get_index_ohlcv(month_start, month_end, "1001")
            if not df.empty:
                # 데이터가 존재하는 가장 마지막 날짜가 곧 해당 월의 최종 영업일
                last_b_day = df.index[-1].date()
                valid_b_dates.append(last_b_day)
        except Exception as e:
            print(f"⚠️ {month_end} 영업일 조회 중 에러 발생: {e}")
            
        # 서버 부하 방지
        time.sleep(0.5)
        
    return valid_b_dates

def main():
    # 1. 실행 파라미터 (명령줄 인자) 설정
    parser = argparse.ArgumentParser(description="Stock Screener Backtest Entry Point")
    parser.add_argument("--start", type=int, default=2019, help="백테스트 시작 연도 (기본: 2019)")
    parser.add_argument("--end", type=int, default=2025, help="백테스트 종료 연도 (기본: 2025)")
    args = parser.parse_args()

    print("==================================================")
    print(f"🚀 백테스트 파이프라인 가동: {args.start}년 ~ {args.end}년")
    print("==================================================\n")

    # 2. 날짜 리스트 생성
    base_dates = generate_quarterly_dates(args.start, args.end)
    print(f"📅 생성된 리밸런싱 기준일: 총 {len(base_dates)}개 분기")

    # 3. 환경 설정 및 인스턴스 초기화 (조립)
    config_path = PROJECT_ROOT / "config" / "params.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            params = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
        return

    # 앞서 만든 캐시 웜업 스크립트를 먼저 돌려두었다고 가정하고, 캐시를 켠 상태로 로더 초기화
    loader = QuantDataLoader(use_cache=True)
    pipeline = QuantPipeline(params, loader)
    engine = BacktestEngine(pipeline, loader)

    # 4. 순수 엔진 실행 (계산 로직 위임)
    print("⏳ 엔진에 날짜 리스트를 주입하고 시뮬레이션을 시작합니다...\n")
    perf_df, port_df = engine.run(base_dates)

    # 5. 결과 산출물 저장 (outputs/ 폴더)
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