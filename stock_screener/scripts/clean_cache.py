import os
import sys
import re
from datetime import datetime
from pathlib import Path

# 단독 실행 시 프로젝트 루트 디렉토리를 시스템 경로에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

# loader의 헬퍼 함수를 재사용하기 위해 import
from data.loader import QuantDataLoader

def clean_cache():
    print("==================================================")
    print("🧹 캐시 청소기 가동: 비영업일(주말/공휴일) 찌꺼기 파일 탐색 중...")
    print("==================================================\n")
    
    # 캐시 디렉토리 접근을 위해 loader 인스턴스화 (API 호출은 안 함)
    loader = QuantDataLoader(use_cache=False)
    cache_dir = loader.cache_dir
    
    if not cache_dir.exists():
        print("❌ 캐시 폴더가 존재하지 않습니다.")
        return

    deleted_count = 0
    
    # 정규식 패턴: 파일명에서 YYYYMMDD 추출
    # 1. universe_20210228.csv 또는 fundamentals_20210228.csv
    single_date_pattern = re.compile(r"^(universe|fundamentals)_(\d{8})\.csv$")
    
    # 2. ohlcv_005930_20210228_20210228.csv
    double_date_pattern = re.compile(r"^ohlcv_.*_(\d{8})_(\d{8})\.csv$")
    
    for file_path in cache_dir.iterdir():
        if not file_path.is_file():
            continue
            
        filename = file_path.name
        
        # 🛡️ DART 재무제표 파일은 안전 구역이므로 무조건 패스
        if filename.startswith("dart_"):
            continue

        is_invalid = False
        
        # 1. 단일 날짜 파일(유니버스, 펀더멘털) 검사
        match_single = single_date_pattern.match(filename)
        if match_single:
            date_str = match_single.group(2)
            target_date = datetime.strptime(date_str, "%Y%m%d").date()
            
            # 헬퍼 함수를 통과시킨 날짜와 비교
            valid_bday = loader._get_nearest_past_bday(target_date)
            if date_str != valid_bday:
                is_invalid = True

        # 2. 이중 날짜 파일(OHLCV) 검사
        match_double = double_date_pattern.match(filename)
        if match_double:
            start_str = match_double.group(1)
            end_str = match_double.group(2)
            
            start_date = datetime.strptime(start_str, "%Y%m%d").date()
            end_date = datetime.strptime(end_str, "%Y%m%d").date()
            
            # 시작일이나 종료일 중 하나라도 영업일이 아니면 무효 처리
            valid_start = loader._get_nearest_past_bday(start_date)
            valid_end = loader._get_nearest_past_bday(end_date)
            
            if start_str != valid_start or end_str != valid_end:
                is_invalid = True

        # 3. 적발된 비영업일 파일 삭제
        if is_invalid:
            print(f"🗑️ 삭제됨: {filename}")
            file_path.unlink()
            deleted_count += 1
            
    print(f"\n✅ 청소 완료! 총 {deleted_count}개의 유령 캐시 파일이 안전하게 삭제되었습니다.")

if __name__ == "__main__":
    clean_cache()