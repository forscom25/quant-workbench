import os
import sys
import yaml
import itertools
import pandas as pd
import time
from datetime import date
from pathlib import Path

# 단독 실행 시 프로젝트 루트 디렉토리를 시스템 경로에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from core.schema import Stage
from core.pipeline import QuantPipeline
from data.loader import QuantDataLoader

def resolve_target_tickers(pipeline: QuantPipeline, base_dates: list[date]) -> set[str]:
    """
    각 base_date에 대해 Stage 1(OHLCV 기반 소외 섹터 발굴)까지만 실행하여,
    재무 데이터 조회가 실제로 필요한 후보 종목 집합(Set)을 도출합니다.
    """
    print("🔍 [1/2] 동적 리졸버 가동: Stage 1 선실행을 통한 타겟 종목 추출 중...")
    target_tickers = set()
    
    for bd in base_dates:
        # DART 호출 없이 가격/거래대금만으로 1단계 단독 실행
        try:
            survivors_df, _ = pipeline.run(bd, stop_after=Stage.STAGE1)
            if not survivors_df.empty:
                target_tickers.update(survivors_df['ticker'].tolist())
        except Exception as e:
            print(f"  ⚠️ {bd} 기준일 Stage 1 실행 중 에러 (건너뜀): {e}")
        time.sleep(2.0)

    print(f"✅ [1/2] 타겟 종목 추출 완료: 총 {len(target_tickers)}개 종목이 후보군으로 선정되었습니다.\n")
    return target_tickers

def warm_up_dart_cache():
    print("==================================================")
    print("🔥 DART 재무 데이터 스마트 캐시 예열 (Warm-up) 시작")
    print("==================================================\n")
    
    # 1. 설정 및 인스턴스 초기화
    config_path = PROJECT_ROOT / "config" / "params.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            params = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
        return
        
    loader = QuantDataLoader(use_cache=True)
    pipeline = QuantPipeline(params, loader)
    
    # 2. 백테스트와 동일하게 KOSPI 영업일 기반으로 생성
    base_dates = loader.get_quarterly_rebalance_dates(2019, 2025)
    
    # 3. 프리 필터(Pre-filter)를 통한 타겟 종목 도출
    target_tickers = resolve_target_tickers(pipeline, base_dates)
    
    if not target_tickers:
        print("❌ 후보군으로 선정된 종목이 없습니다. 파이프라인 설정을 확인해주세요.")
        return

    # 4. 재무 데이터 수집 범위 설정
    years = list(range(2018, 2026)) # 2019년 분석을 위해 2018년부터 필요
    report_codes = ['11013', '11012', '11014', '11011'] 
    
    total_combinations = len(target_tickers) * len(years) * len(report_codes)
    print(f"🎯 웜업 대상: 종목 {len(target_tickers)}개 | 연도 {len(years)}년 | 분기 4개")
    print(f"📦 총 예상 API 호출 조합 수: {total_combinations:,}건 (전 종목 대비 압도적 감소)\n")
    
    # 5. 캐시 적재 루프 (이어달리기 지원)
    success_count = 0
    error_count = 0
    
    print("⏳ [2/2] 재무 데이터 DART API 조달 및 캐싱 시작...")
    try:
        for ticker, year, rc in itertools.product(target_tickers, years, report_codes):
            try:
                # CFS(연결) 우선, 없으면 OFS(개별)로 가져오도록 유도
                _ = loader.get_financial_statements(ticker, year, rc, fs_div='CFS')
                success_count += 1
                
            except RuntimeError as e:
                # DART API 일일 한도 초과 감지
                if "한도" in str(e) or "limit" in str(e).lower():
                    print(f"\n🚨 [일일 호출량 초과] {year}년 {rc} 보고서 수집 중 DART 한도(10,000건) 도달.")
                    break
                else:
                    error_count += 1
            except Exception:
                # 상장 전이거나 해당 분기 보고서가 없는 경우 (자연스러운 현상)
                error_count += 1
                
            # 진행 상황 모니터링 (100건 단위 출력)
            current_total = success_count + error_count
            if current_total % 100 == 0:
                print(f"  ▶ 진행률: {current_total:,} / {total_combinations:,} 완료 (수집/캐시적중: {success_count:,} | 없음/에러: {error_count:,})")
                
    except KeyboardInterrupt:
        print("\n🛑 사용자에 의해 강제 중단되었습니다.")
        
    print("\n==================================================")
    print("✅ 스마트 캐시 예열 작업 종료")
    print(f" - 누적 수집 및 캐시 적중: {success_count:,}건")
    print(f" - 데이터 없음(신규상장 등): {error_count:,}건")
    print("💡 내일 다시 실행하면 멈춘 곳부터 API 호출 없이 빠르게 건너뛴 후 이어서 수집합니다.")

if __name__ == "__main__":
    warm_up_dart_cache()