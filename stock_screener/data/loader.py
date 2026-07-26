import os
import json
import time
import pandas as pd
from pykrx import stock
import FinanceDataReader as fdr
import OpenDartReader
from dotenv import load_dotenv
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict
import warnings

class QuantDataLoader:
    def __init__(self, use_cache: bool = True, cache_days: int = 30):
        load_dotenv()
        self.dart_key = os.getenv("DART_API_KEY")
        self.krx_key = os.getenv("KRX_API_KEY")
        
        if not self.dart_key:
            raise ValueError("[오류] DART_API_KEY가 .env 파일에 없습니다.")
            
        self.dart = OpenDartReader(self.dart_key)
        self.use_cache = use_cache
        self.cache_days = cache_days
        self.cache_dir = Path("data/cache")
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        # endpoints.json 로드 (Rate Limit 및 설정)
        # [수정 4] 상대 경로 취약점 해결 -> 절대 경로 기반 로드
        try:
            config_path = Path(__file__).resolve().parent.parent / "config" / "endpoints.json"
            with open(config_path, "r", encoding="utf-8") as f:
                self.endpoints = json.load(f)
            self.dart_retries = self.endpoints.get("DART", {}).get("max_retries", 3)
        except FileNotFoundError:
            self.dart_retries = 3

        # sj_div(재무제표 종류) 필터가 추가된 다차원 매핑 룰
        self.ACCOUNT_MAPPING = {
            "revenue": {
                "sj": "IS", "ids": ["ifrs-full_Revenue"],
                "names": ["매출액", "영업수익"]
                },
            "cogs": { # 매출원가 (매출총이익률 GPM 계산용)
                "sj": "IS", "ids": ["ifrs-full_CostOfSales"],
                "names": ["매출원가", "영업비용"]
                },
            "gross_profit": { # 매출총이익
                "sj": "IS", "ids": ["ifrs-full_GrossProfit"],
                "names": ["매출총이익"]
                },
            "sga": { # 판매비와관리비 (턴어라운드 핵심 지표)
                "sj": "IS", "ids": ["dart_SellingGeneralAdministrativeExpenses"],
                "names": ["판매비와관리비", "판매비 및 일반관리비"]
                },
            "inventory": { # 재고자산 (재고회전율 계산용)
                "sj": "BS", "ids": ["ifrs-full_Inventories"],
                "names": ["재고자산"]
                },
            "operating_income": { # 영업이익
                "sj": "IS", "ids": ["dart_OperatingIncomeLoss"],
                "names": ["영업이익"]
                },
            "net_income": { # 당기순이익
                "sj": "IS", "ids": ["ifrs-full_ProfitLoss"],
                "names": ["당기순이익", "연결당기순이익"]
                },
            "operating_cash_flow": { # 영업활동현금흐름 (이익 품질 검증용)
                "sj": "CF", "ids": ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
                "names": ["영업활동현금흐름"]
                }
        }

    def _is_cache_valid(self, filepath: Path) -> bool:
        """캐시 유효기간(30일) 검증"""
        if not filepath.exists():
            return False
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        return (datetime.now() - mtime).days < self.cache_days

    def get_kospi_universe(self, base_date: date) -> pd.DataFrame:
        """
        [미래 정보 참조 방지 및 하이브리드 매핑 적용]
        Point-in-Time 유니버스를 생성하며, 성능 향상을 위한 캐싱과 
        생존편향(Survivorship Bias) 모니터링 로직을 포함합니다.
        """
        date_str = base_date.strftime("%Y%m%d")
        cache_file = self.cache_dir / f"universe_{date_str}.csv"
        
        # [수정] 성능 최적화: 유니버스 스냅샷 로컬 캐싱 적용
        if self.use_cache and self._is_cache_valid(cache_file):
            # ticker는 005930처럼 앞의 0이 보존되어야 하므로 문자열(str) 타입 강제 지정
            # [완벽 수정] 상단 캐시 읽기: 인코딩 명시 추가 (BOM 및 0 잘림 방지)
            return pd.read_csv(cache_file, dtype={'ticker': str}, encoding='utf-8-sig')
            
        df = stock.get_market_cap(date_str, market="KOSPI")
        if df.empty:
            raise ValueError(f"{date_str} 기준 KOSPI 데이터가 없습니다. (휴장일 가능성)")
            
        df = df.reset_index()
        df = df.rename(columns={'티커': 'ticker', '종가': 'close_price', '시가총액': 'market_cap'})
        df = df[['ticker', 'close_price', 'market_cap']]
        
        # 우선주 배제 (휴리스틱)
        df = df[df['ticker'].str.endswith('0')].copy()
        
        names = {t: stock.get_market_ticker_name(t) for t in df['ticker'].unique()}
        df['name'] = df['ticker'].map(names)
        
        fdr_df = fdr.StockListing('KOSPI')[['Code', 'Sector']]
        fdr_df.columns = ['ticker', 'sector']
        
        df = pd.merge(df, fdr_df, on='ticker', how='left')
        
        # [수정] 생존편향(Survivorship Bias) 트래킹 로깅
        missing_sector_ratio = df['sector'].isna().mean()
        if missing_sector_ratio > 0.05:
            warnings.warn(
                f"[{date_str}] 섹터 매핑 실패율 {missing_sector_ratio:.1%} "
                f"— 상장폐지 종목 다수 포함 가능성 (백테스트 시 생존편향 주의)"
            )
            
        df['sector'] = df['sector'].fillna('기타')
        df = df[['ticker', 'name', 'sector', 'close_price', 'market_cap']]
        
        # [수정] 생성된 데이터프레임 캐시 저장
        if self.use_cache:
            df.to_csv(cache_file, index=False, encoding='utf-8-sig')
            
        return df

    def get_financial_statements(self, ticker: str, year: int, report_code: str = '11011') -> Optional[pd.DataFrame]:
        """재시도(Retry) 및 캐시 무효화가 적용된 DART 데이터 로더"""
        cache_file = self.cache_dir / f"dart_{ticker}_{year}_{report_code}.csv"
        
        if self.use_cache and self._is_cache_valid(cache_file):
            return pd.read_csv(cache_file)

        for attempt in range(self.dart_retries):
            try:
                fs_df = self.dart.finstate_all(ticker, year, reprt_code=report_code)
                if fs_df is not None and not fs_df.empty:
                    if self.use_cache:
                        fs_df.to_csv(cache_file, index=False, encoding='utf-8-sig')
                    return fs_df
                break  # 정상 응답이나 데이터가 없는 경우
            except Exception as e:
                if attempt == self.dart_retries - 1:
                    warnings.warn(f"[DART API 최종 실패] {ticker}: {e}")
                    return None
                time.sleep(1) # Rate limit 방어
        return None

    def parse_standardized_financials(self, ticker: str, year: int, report_code: str = '11011') -> Dict[str, float]:
        """
        [CFS/OFS 분리 및 sj_div 필터 적용]
        연결재무제표(CFS)를 우선 탐색하고, 없을 경우 별도재무제표(OFS)로 폴백합니다.
        (주의: 1, 3분기 누적 데이터는 Stage 3 계산 로직에서 차분 처리 필요)
        """
        fs_df = self.get_financial_statements(ticker, year, report_code)
        standard_metrics = {key: float('nan') for key in self.ACCOUNT_MAPPING.keys()}
        
        if fs_df is None or fs_df.empty or 'thstrm_amount' not in fs_df.columns:
            return standard_metrics

        # 1. 연결(CFS) / 별도(OFS) 분리 및 폴백
        target_df = fs_df[fs_df['fs_div'] == 'CFS']
        if target_df.empty:
            target_df = fs_df[fs_df['fs_div'] == 'OFS']
            
        if target_df.empty:
            return standard_metrics

        # 2. 재무제표 종류(sj_div) 및 계정 매핑
        for standard_key, rules in self.ACCOUNT_MAPPING.items():
            # sj_div (BS: 재무상태표, IS: 손익계산서, CF: 현금흐름표) 필터링
            sj_filtered = target_df[target_df['sj_div'].str.contains(rules["sj"], na=False)]
            
            for _, row in sj_filtered.iterrows():
                acc_id = str(row.get('account_id', '')).strip()
                acc_nm = str(row.get('account_nm', '')).strip()
                amount_str = str(row.get('thstrm_amount', '')).replace(',', '')
                
                if not amount_str or not amount_str.lstrip('-').isdigit():
                    continue
                amount = float(amount_str)

                if acc_id in rules["ids"] or any(name in acc_nm for name in rules["names"]):
                    standard_metrics[standard_key] = amount
                    break

        return standard_metrics