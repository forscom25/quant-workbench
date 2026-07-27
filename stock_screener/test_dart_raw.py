import os
import OpenDartReader
from dotenv import load_dotenv

def check_raw_dart_data():
    print("DART API 원본 데이터 구조 확인을 시작합니다...\n")
    
    # 1. 환경변수 로드 및 DART 인스턴스 생성
    load_dotenv()
    dart_key = os.getenv("DART_API_KEY")
    if not dart_key:
        print("❌ DART_API_KEY가 없습니다. .env 파일을 확인해주세요.")
        return
        
    dart = OpenDartReader(dart_key)
    
    # 2. 삼성전자(005930), 2023년, 사업보고서(11011) 단일회사 전체 재무제표 요청
    ticker = '005930'
    year = 2023
    
    print(f"[{ticker}] {year}년 사업보고서(CFS: 연결재무제표) 요청 중...")
    
    # OpenDartReader의 finstate_all 호출
    # fs_div='CFS' (연결) 가 기본값으로 들어갑니다.
    fs_df = dart.finstate_all(ticker, year, reprt_code='11011')
    
    if fs_df is None or fs_df.empty:
        print("❌ 데이터를 불러오지 못했습니다. API 키나 한도를 확인하세요.")
        return
        
    # 3. 반환된 데이터프레임의 구조(컬럼) 확인
    print("\n✅ 데이터 로드 성공!")
    print("-" * 50)
    print("1. 데이터프레임에 존재하는 실제 컬럼 목록:")
    print(fs_df.columns.tolist())
    print("-" * 50)
    
    # 4. 실제 데이터 첫 5줄 확인
    print("\n2. 원본 데이터 미리보기 (상위 5줄):")
    print(fs_df.head().to_string())
    print("-" * 50)

if __name__ == "__main__":
    check_raw_dart_data()