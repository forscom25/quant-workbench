import os
import pandas as pd
import FinanceDataReader as fdr
import OpenDartReader
from dotenv import load_dotenv
from datetime import date
from pathlib import Path
from typing import Optional, Dict
import warnings

class QuantDataLoader:
    def __init__(self, use_cache: bool = True):
        load_dotenv()
        self.dart_key = os.getenv("DART_API_KEY")
        self.krx_key = os.getenv("KRX_API_KEY")
        
        if not self.dart_key:
            raise ValueError("[오류] DART_API_KEY가 .env 파일에 없습니다.")
            
        self.dart = OpenDartReader(self.dart_key)
        self.use_cache = use_cache
        self.cache_dir = Path("data/cache")
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        # ==========================================
        # [핵심] DART 재무제표 계정명 표준화 매핑 룰
        # ==========================================
        self.ACCOUNT_MAPPING = {
            "revenue": {
                "ids": ["ifrs-full_Revenue"],
                "names": ["매출액", "영업수익", "수익(매출액)"]
            },
            "cogs": { # 매출원가 (매출총이익률 GPM 계산용)
                "ids": ["ifrs-full_CostOfSales"],
                "names": ["매출원가", "영업비용"]
            },
            "gross_profit": { # 매출총이익
                "ids": ["ifrs-full_GrossProfit"],
                "names": ["매출총이익"]
            },
            "sga": { # 판매비와관리비 (턴어라운드 핵심 지표)
                "ids": ["dart_SellingGeneralAdministrativeExpenses"],
                "names": ["판매비와관리비", "판매비와 관리비", "판매비 및 일반관리비"]
            },
            "inventory": { # 재고자산 (재고회전율 계산용)
                "ids": ["ifrs-full_Inventories"],
                "names": ["재고자산"]
            },
            "operating_income": { # 영업이익
                "ids": ["dart_OperatingIncomeLoss"],
                "names": ["영업이익", "영업이익(손실)"]
            },
            "net_income": { # 당기순이익
                "ids": ["ifrs-full_ProfitLoss"],
                "names": ["당기순이익", "당기순이익(손실)", "연결당기순이익"]
            },
            "operating_cash_flow": { # 영업활동현금흐름 (이익 품질 검증용)
                "ids": ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
                "names": ["영업활동현금흐름", "영업활동으로 인한 현금흐름"]
            }
        }

    # ==========================================
    # 1. FDR: 시장 및 종목 기본 데이터 (Universe)
    # ==========================================
    def get_kospi_universe(self, base_date: Optional[date] = None) -> pd.DataFrame:
        """
        FDR을 활용하여 KOSPI 전 종목의 시가총액, 종가 등 기본 정보를 가져옵니다.
        (FDR의 'KRX-MARCAP' 등을 활용해 특정 시점 스냅샷 구축)
        """
        # 현재 FDR은 최신 기준 시가총액을 'KOSPI' 리스팅에서 제공합니다.
        # 과거 특정 시점(base_date)이 필요할 경우 별도의 과거 데이터 처리 로직 필요
        print("FDR: KOSPI 유니버스 데이터를 불러옵니다...")
        df = fdr.StockListing('KOSPI')
        
        # 필요한 컬럼만 추출 및 이름 표준화
        if 'Marcap' in df.columns:
            df = df[['Code', 'Name', 'Sector', 'Close', 'Marcap']]
            df.columns = ['ticker', 'name', 'sector', 'close_price', 'market_cap']
        
        return df

    # ==========================================
    # 2. OpenDART: 재무제표 펀더멘털 데이터
    # ==========================================
    def get_financial_statements(self, ticker: str, year: int, report_code: str = '11011') -> Optional[pd.DataFrame]:
        """
        특정 기업의 재무제표를 불러옵니다.
        report_code: 11011(사업보고서), 11012(반기), 11013(1분기), 11014(3분기)
        """
        cache_file = self.cache_dir / f"dart_{ticker}_{year}_{report_code}.csv"
        
        if self.use_cache and cache_file.exists():
            return pd.read_csv(cache_file)

        try:
            # finstate_all은 전체 재무제표, finstate는 주요 계정만 제공
            fs_df = self.dart.finstate_all(ticker, year, reprt_code=report_code)
            
            if fs_df is not None and not fs_df.empty:
                if self.use_cache:
                    fs_df.to_csv(cache_file, index=False, encoding='utf-8-sig')
                return fs_df
            else:
                return None
                
        except Exception as e:
            print(f"[DART API 오류] {ticker}: {e}")
            return None

    # ==========================================
    # 3. KRX: 특수 데이터 (수급, 공매도 등) - 프록시 패턴 적용
    # ==========================================
    def get_investor_trading_volume(self, ticker: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        투자자별(외국인/기관) 수급 데이터를 가져옵니다. 
        API 키가 승인되기 전까지는 경고를 출력하고 None을 반환하여 파이프라인 중단을 막습니다.
        """
        if not self.krx_key:
            warnings.warn(
                f"KRX_API_KEY가 없습니다. '{ticker}'의 투자자별 수급 데이터 수집을 건너뜁니다.", 
                UserWarning
            )
            return None
            
        # TODO: 추후 KRX Open API 승인 시 요청 로직 구현
        # headers = {"authorization": f"Bearer {self.krx_key}"}
        # response = requests.get(url, headers=headers)
        # return pd.DataFrame(response.json())
        pass

    # ==========================================
    # 4. 데이터 정제: 계정 표준화 파서 (Parser)
    # ==========================================
    def parse_standardized_financials(self, ticker: str, year: int, report_code: str = '11011') -> Dict[str, float]:
        """
        raw 재무제표 데이터를 불러와 사전에 정의된 ACCOUNT_MAPPING 룰에 따라
        표준화된 딕셔너리 형태로 변환하여 반환합니다.
        """
        fs_df = self.get_financial_statements(ticker, year, report_code)
        
        # 반환할 표준화된 데이터 템플릿
        standard_metrics = {key: float('nan') for key in self.ACCOUNT_MAPPING.keys()}
        
        if fs_df is None or fs_df.empty:
            return standard_metrics

        # 최신 기수(당기) 데이터의 금액 컬럼명 (보통 'thstrm_amount'로 제공됨)
        target_col = 'thstrm_amount' 
        if target_col not in fs_df.columns:
            return standard_metrics

        # 데이터프레임 순회하며 매핑
        for _, row in fs_df.iterrows():
            acc_id = str(row.get('account_id', '')).strip()
            acc_nm = str(row.get('account_nm', '')).strip()
            amount_str = str(row.get(target_col, '')).replace(',', '')
            
            if not amount_str or not amount_str.lstrip('-').isdigit():
                continue
                
            amount = float(amount_str)

            # 매핑 룰과 대조
            for standard_key, rules in self.ACCOUNT_MAPPING.items():
                if pd.isna(standard_metrics[standard_key]):  # 아직 값을 찾지 못한 경우만
                    # 1순위: K-IFRS 표준 코드로 매칭
                    if acc_id in rules["ids"]:
                        standard_metrics[standard_key] = amount
                        break
                    # 2순위: 텍스트 이름으로 매칭
                    elif any(name in acc_nm for name in rules["names"]):
                        standard_metrics[standard_key] = amount
                        break

        return standard_metrics