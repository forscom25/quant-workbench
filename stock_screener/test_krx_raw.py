import os
from pykrx import stock
from dotenv import load_dotenv

def check_raw_krx_data():
    print("KRX 원본 데이터 및 환경변수 확인을 시작합니다...\n")

    # 1. 환경 변수 체크 (.env 로드)
    load_dotenv()
    krx_id = os.getenv("KRX_ID")
    krx_pw = os.getenv("KRX_PW")

    print("1. 환경 변수 체크:")
    print(f" - KRX_ID 설정 여부: {'✅ 설정됨' if krx_id else '❌ 없음'}")
    print(f" - KRX_PW 설정 여부: {'✅ 설정됨' if krx_pw else '❌ 없음'}\n")

    # 2. pykrx를 통한 KOSPI 시가총액 데이터 요청
    test_date = "20230724"  # 테스트했던 날짜와 동일하게 설정
    print(f"2. {test_date} 기준 KOSPI 시가총액 데이터 요청 중...")

    try:
        # 원본 데이터 호출
        df = stock.get_market_cap(test_date, market="KOSPI")

        if df is None or df.empty:
            print("❌ 반환된 데이터가 비어있습니다. 로그인 문제이거나 휴장일/데이터 차단일 수 있습니다.")
            return

        print("✅ 데이터 로드 성공!")
        print("-" * 50)
        print("3. 데이터프레임 구조(컬럼) 확인:")
        print(df.columns.tolist())
        print("-" * 50)

        print("\n4. 원본 데이터 미리보기 (상위 5줄):")
        print(df.head().to_string())
        print("-" * 50)

    except Exception as e:
        print("\n❌ 데이터를 불러오는 중 에러가 발생했습니다!")
        print(f"에러 메시지: {e}")

if __name__ == "__main__":
    check_raw_krx_data()