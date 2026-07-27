import os
from dotenv import load_dotenv
import FinanceDataReader as fdr
from pykrx import stock

def debug_sector_match():
    # 환경변수 로드 추가! (이 부분이 빠져있었습니다)
    load_dotenv()
    
    print("🔍 섹터 매핑 100% 실패 원인 분석을 시작합니다...\n")
    
    # 1. pykrx 데이터 확인 (기준 데이터)
    df_krx = stock.get_market_cap("20230724", market="KOSPI").reset_index()
    krx_tickers = df_krx['티커'].head(3).tolist()
    krx_type = type(df_krx['티커'].iloc[0])
    
    print("1. pykrx 종목코드 (ticker) 상태:")
    print(f" - 샘플: {krx_tickers}")
    print(f" - 타입: {krx_type}\n")
    
    # 2. FDR KRX-DESC 데이터 확인 (붙일 데이터)
    fdr_df = fdr.StockListing('KRX-DESC')
    fdr_tickers = fdr_df['Code'].head(3).tolist()
    fdr_type = type(fdr_df['Code'].iloc[0])
    
    print("2. FinanceDataReader 종목코드 (Code) 상태:")
    print(f" - 샘플: {fdr_tickers}")
    print(f" - 타입: {fdr_type}\n")
    
    # 3. FDR 섹터 데이터 존재 여부 확인
    valid_sectors = fdr_df['Sector'].dropna()
    print("3. FinanceDataReader 섹터 (Sector) 상태:")
    print(f" - 정상 데이터 개수: {len(valid_sectors)}개 / 전체 {len(fdr_df)}개")
    if len(valid_sectors) > 0:
        print(f" - 섹터 샘플: {valid_sectors.head(3).tolist()}")
    else:
        print(" ❌ [경고] 섹터 데이터가 모두 비어있습니다!")

if __name__ == "__main__":
    debug_sector_match()