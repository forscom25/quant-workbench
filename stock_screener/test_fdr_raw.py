import FinanceDataReader as fdr

def check_fdr_columns():
    print("FinanceDataReader(fdr) KOSPI 컬럼 확인을 시작합니다...\n")
    
    # 1. KOSPI 기본 리스트 호출
    df_kospi = fdr.StockListing('KOSPI')
    print("1. 'KOSPI' 호출 시 실제 컬럼 목록:")
    print(df_kospi.columns.tolist())
    print("-" * 50)
    
    # 2. KRX 종목 상세(KRX-DESC) 호출 (보통 여기에 섹터 정보가 있습니다)
    df_desc = fdr.StockListing('KRX-DESC')
    print("\n2. 'KRX-DESC' 호출 시 실제 컬럼 목록:")
    print(df_desc.columns.tolist())
    print("-" * 50)

if __name__ == "__main__":
    check_fdr_columns()