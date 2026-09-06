import os
import json
import time
import logging
import pandas as pd
import numpy as np
from pykrx import stock
import FinanceDataReader as fdr
import OpenDartReader
from dotenv import load_dotenv
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict
import warnings

class QuantDataLoader:
    def __init__(self, use_cache: bool = True, cache_days: int = 30,
                 ttm_denominator: str = "latest_snapshot", dart_cache_days: int = 3650):
        load_dotenv()
        self.dart_key = os.getenv("DART_API_KEY")
        self.krx_key = os.getenv("KRX_API_KEY")

        if not self.dart_key:
            raise ValueError("[오류] DART_API_KEY가 .env 파일에 없습니다.")

        self.dart = OpenDartReader(self.dart_key)
        self.use_cache = use_cache
        # 유니버스/시세/펀더멘털처럼 "최신성"이 중요한 캐시의 기본 유효기간
        self.cache_days = cache_days
        # DART 공시 재무제표는 확정 공시 후 사실상 불변(드문 정정공시 예외)이므로 별도의 훨씬 긴
        # 유효기간을 둔다. cache_days(30일)를 그대로 썼다면 30일 지난 과거 확정 데이터까지
        # "만료"로 오판해 불필요하게 DART API를 재호출하며 일일 호출 한도를 낭비하게 됨.
        self.dart_cache_days = dart_cache_days

        # params.yaml의 global.ttm_denominator에 대응 — TTM(4분기 합산) 분자와 짝지을 BS 분모를
        # 가장 최근 분기말 스냅샷으로 볼지("latest_snapshot"), 4개 분기 평균으로 볼지("avg_4q").
        # avg_4q는 자사주 매입/유상증자 등으로 중간에 분모가 급변하는 경우의 외란을 완화한다.
        if ttm_denominator not in ("latest_snapshot", "avg_4q"):
            raise ValueError(f"ttm_denominator는 'latest_snapshot' 또는 'avg_4q'여야 합니다: {ttm_denominator}")
        self.ttm_denominator = ttm_denominator

        # 🚨 수정된 부분: loader.py 파일의 위치(data 폴더)를 기준으로 절대 경로 고정
        base_dir = Path(__file__).resolve().parent
        self.cache_dir = base_dir / "cache"

        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

        # endpoints.json 로드 (Rate Limit 및 설정)
        # [수정 4] 상대 경로 취약점 해결 -> 절대 경로 기반 로드
        try:
            config_path = Path(__file__).resolve().parent.parent / "config" / "endpoints.json"
            with open(config_path, "r", encoding="utf-8") as f:
                self.endpoints = json.load(f)
            self.dart_retries = self.endpoints.get("DART", {}).get("max_retries", 3)
        except FileNotFoundError:
            self.endpoints = {}
            self.dart_retries = 3

        # pykrx 호출(get_market_cap 등)은 이전까지 재시도 로직이 전혀 없어, 네트워크 순단
        # 한 번에 전체 다년치 백테스트가 죽는 원인이 됐음 — endpoints.json에 이미 있던
        # (그러나 지금까지 아무 데서도 읽지 않던) KRX.max_retries를 실제로 배선.
        self.krx_retries = self.endpoints.get("KRX", {}).get("max_retries", 3)

        # config 로드 블록 하단에 추가 (DART API 일일 호출량 관리)
        self.dart_daily_limit = self.endpoints.get("DART", {}).get("daily_limit", 9500)
        # 🚨 [수정] dart_call_count를 프로세스 메모리에만 두면 재시작할 때마다 0으로 리셋되어,
        # 같은 날 여러 번 실행(크래시 후 재시도 등)할 경우 로컬 카운터는 계속 여유가 있다고
        # 착각하지만 DART 서버 쪽 실제 일일 한도는 프로세스와 무관하게 계속 누적되는 문제가
        # 실제로 발생함. 날짜별 카운트를 파일로 영속화해 프로세스 재시작에도 이어지게 한다.
        self._dart_count_file = self.cache_dir / "dart_call_state.json"
        self.dart_call_count = self._load_dart_call_count()

        # sj_div(재무제표 종류) 필터가 추가된 다차원 매핑 룰
        self.ACCOUNT_MAPPING = {
            "revenue": {
                "sj": "IS",
                "ids": ["ifrs-full_Revenue"],
                "names": ["매출액", "영업수익"]
                },
            "cogs": { # 매출원가 (매출총이익률 GPM 계산용)
                "sj": "IS",
                "ids": ["ifrs-full_CostOfSales"],
                "names": ["매출원가"]
                },
            "gross_profit": { # 매출총이익
                "sj": "IS",
                "ids": ["ifrs-full_GrossProfit"],
                "names": ["매출총이익"]
                },
            "sga": { # 판매비와관리비 (턴어라운드 핵심 지표)
                "sj": "IS",
                "ids": ["dart_SellingGeneralAdministrativeExpenses"],
                "names": ["판매비와관리비", "판매비와 관리비", "판매비와일반관리비", "판매비 및 일반관리비", "판매관리비"]
                },
            "inventory": { # 재고자산 (재고회전율 계산용)
                "sj": "BS",
                "ids": ["ifrs-full_Inventories"],
                "names": ["재고자산"]
                },
            "operating_income": { # 영업이익
                "sj": "IS",
                "ids": ["dart_OperatingIncomeLoss"],
                "names": ["영업이익"]
                },
            "net_income": { # 당기순이익
                "sj": "IS",
                "ids": ["ifrs-full_ProfitLoss"],
                "names": ["당기순이익", "연결당기순이익"]
                },
            "operating_cash_flow": { # 영업활동현금흐름 (이익 품질 검증용)
                "sj": "CF",
                "ids": ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
                "names": ["영업활동현금흐름"]
                },
            "total_assets": { # 자산총계
                "sj": "BS",
                "ids": ["ifrs-full_Assets"],
                "names": ["자산총계"]
                },
            "total_liabilities": { # 부채총계
                "sj": "BS",
                "ids": ["ifrs-full_Liabilities"],
                "names": ["부채총계"]
                },
            "total_equity": { # 자본총계
                "sj": "BS",
                "ids": ["ifrs-full_Equity"],
                "names": ["자본총계"]
                },
            "interest_expense": {
                # 이자비용. 우선순위: ①정확계정 → ②P&L "이자비용" 세부항목
                # → ③현금흐름표 이자지급 조정 라인(cf_ids) → ④광의 금융비용(fallback_names, 최후 수단).
                # data/cache 표본 검증 결과 "금융비용/금융원가"를 그대로 쓰면 FX·파생 손익이 섞여
                # 실제 이자지급액 대비 수십~수백 배 괴리가 나는 종목이 다수(150개 중 87개) 확인됨.
                "sj": "IS",
                "ids": ["ifrs-full_InterestExpense", "dart_InterestExpense"],
                "names": ["이자비용"],
                "cf_ids": ["dart_AdjustmentsForInterestExpenses", "ifrs-full_InterestPaidClassifiedAsOperatingActivities"],
                "fallback_names": ["금융원가", "금융비용"],
                },
            "total_borrowings": {
                # 총차입금 (ROIC 투하자본 계산용). 회사마다 유동/비유동/사채 등으로 계정이 쪼개져
                # 보고되므로 매칭되는 모든 행을 합산한다(단일 최고 매치가 아님). 매치 실패 시 무차입으로 간주(0).
                "sj": "BS",
                "ids": [
                    "ifrs-full_ShorttermBorrowings", "ifrs-full_LongtermBorrowings",
                    "ifrs-full_CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
                    "ifrs-full_CurrentPortionOfLongtermBorrowings",
                    "ifrs-full_NoncurrentPortionOfNoncurrentLoansReceived",
                    "ifrs-full_CurrentLoansReceivedAndCurrentPortionOfNoncurrentLoansReceived",
                    "dart_LongTermBorrowingsGross",
                    "ifrs_ShorttermBorrowings",
                    ],
                "names": ["차입금", "사채"],
                }
        }

    def _load_dart_call_count(self) -> int:
        """
        오늘 날짜 기준 누적 DART 호출 수를 영속 파일(dart_call_state.json)에서 복구합니다.
        파일이 없거나, 저장된 날짜가 오늘이 아니면(새로운 날) 0부터 새로 셉니다.
        use_cache=False면 영속화 자체를 건너뛰고 항상 0에서 시작합니다(기존 동작 유지).
        """
        if not self.use_cache:
            return 0
        try:
            with open(self._dart_count_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("date") == date.today().isoformat():
                return int(state.get("count", 0))
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            pass
        return 0

    def _persist_dart_call_count(self):
        """현재까지의 DART 호출 수를 오늘 날짜와 함께 파일에 저장 — 프로세스가 중간에
        죽거나 재시작돼도 같은 날엔 이어서 카운트되도록 한다."""
        if not self.use_cache:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._dart_count_file, "w", encoding="utf-8") as f:
                json.dump({"date": date.today().isoformat(), "count": self.dart_call_count}, f)
        except Exception as e:
            self.logger.warning(f"[DART 호출 카운트 저장 실패] {e}")

    def _is_cache_valid(self, filepath: Path, cache_days: Optional[int] = None) -> bool:
        """캐시 유효기간 검증. cache_days 미지정 시 self.cache_days(기본 30일) 사용 —
        DART 재무제표처럼 불변 데이터를 캐싱하는 호출부는 self.dart_cache_days를 명시적으로 전달한다."""
        if not filepath.exists():
            return False
        effective_days = cache_days if cache_days is not None else self.cache_days
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        return (datetime.now() - mtime).days < effective_days

    def _fetch_with_retry(self, fetch_fn, label: str, retries: Optional[int] = None, backoff_sec: float = 1.5):
        """
        pykrx/FDR 등 외부 API 호출을 감싸 일시적 네트워크 오류(순단 등)에 재시도합니다.
        fetch_fn: 인자 없이 즉시 호출 가능한 콜러블 (예: lambda: stock.get_market_cap(date_str, market="KOSPI"))
        모든 재시도가 소진되면 마지막 예외를 그대로 올려보내며, 호출부가 이를 잡아 결측(구제) 처리하거나
        (stage4처럼) BacktestEngine.run()의 분기 단위 예외 처리로 흡수되도록 한다.
        """
        attempts = retries if retries is not None else self.krx_retries
        last_err = None
        for attempt in range(attempts):
            try:
                return fetch_fn()
            except Exception as e:
                last_err = e
                self.logger.warning(f"[{label} 재시도 {attempt + 1}/{attempts}] {e}")
                if attempt < attempts - 1:
                    time.sleep(backoff_sec)
        self.logger.error(f"[{label} 최종 실패] {attempts}회 재시도 후에도 실패: {last_err}")
        raise last_err

    def _get_nearest_past_bday(self, target_date: date) -> str:
        """
        주어진 target_date를 포함하여 가장 가까운 '과거'의 영업일(KOSPI 기준)을 찾아 
        'YYYYMMDD' 문자열 형태로 반환합니다.
        (pykrx 내부 캘린더 버그 방지 및 미래 정보 참조(Look-ahead bias) 원천 차단)
        """
        from dateutil.relativedelta import relativedelta
        import FinanceDataReader as fdr
        
        # target_date 기준 과거 15일(긴 명절 연휴를 고려해 넉넉히) 데이터 조회
        start_date = target_date - relativedelta(days=15)
        
        try:
            # KS11(코스피 지수) 데이터를 통해 실제 장이 열린 날짜만 가져옴
            df = fdr.DataReader('KS11', start_date, target_date)
            
            if not df.empty:
                # 가장 마지막 인덱스가 target_date와 같거나 가장 가까운 과거의 영업일이 됨
                return df.index[-1].strftime("%Y%m%d")
                
        except Exception as e:
            self.logger.warning(f"[과거 영업일 탐색 실패] {target_date}: {e}")
            
        # 만약 API 실패 등의 이유로 조회하지 못했다면 원래 날짜를 그대로 반환 (최후의 보루)
        return target_date.strftime("%Y%m%d")

    def get_kospi_universe(self, base_date: date) -> pd.DataFrame:
        """
        [미래 정보 참조 방지 및 하이브리드 매핑 적용]
        Point-in-Time 유니버스를 생성하며, 성능 향상을 위한 캐싱과
        생존편향(Survivorship Bias) 모니터링 로직을 포함합니다.

        [알려진 한계] ticker/close_price/market_cap은 base_date 기준 point-in-time 데이터이지만,
        sector(업종)는 아니다 — fdr.StockListing('KRX-DESC')가 날짜 인자를 받지 않아 항상
        "캐시를 생성한 시점"의 현재 업종 분류가 모든 base_date에 동일하게 붙는다. 실제로
        2019-09-30 vs 2025-09-30 캐시를 비교해도 공통 종목 747개 전부 섹터가 동일함을 확인함
        (KRX 업종 재분류가 드물게만 일어나 실질적 영향은 제한적일 것으로 판단, 2026-09-06 검토).
        완전한 해결은 과거 시점 업종 분류를 제공하는 데이터 소스가 필요해 현재 범위 밖으로 보류.
        """
        date_str = self._get_nearest_past_bday(base_date)
        cache_file = self.cache_dir / f"universe_{date_str}.csv"
        
        # [수정] 성능 최적화: 유니버스 스냅샷 로컬 캐싱 적용
        if self.use_cache and self._is_cache_valid(cache_file):
            # ticker는 005930처럼 앞의 0이 보존되어야 하므로 문자열(str) 타입 강제 지정
            # [완벽 수정] 상단 캐시 읽기: 인코딩 명시 추가 (BOM 및 0 잘림 방지)
            return pd.read_csv(cache_file, dtype={'ticker': str}, encoding='utf-8-sig')
            
        df = self._fetch_with_retry(
            lambda: stock.get_market_cap(date_str, market="KOSPI"), label=f"KOSPI 시가총액({date_str})"
        )
        if df.empty:
            raise ValueError(f"{date_str} 기준 KOSPI 데이터가 없습니다. (휴장일 가능성)")
            
        df = df.reset_index()
        df = df.rename(columns={'티커': 'ticker', '종가': 'close_price', '시가총액': 'market_cap'})
        df = df[['ticker', 'close_price', 'market_cap']]
        
        # 우선주 배제 (휴리스틱)
        df = df[df['ticker'].str.endswith('0')].copy()
        
        names = {t: stock.get_market_ticker_name(t) for t in df['ticker'].unique()}
        df['name'] = df['ticker'].map(names)
        
        # ⚠️ 날짜 인자 없음 — base_date와 무관하게 항상 "현재" 업종 분류를 반환함.
        # point-in-time이 아니라는 한계를 이 함수 docstring에 명시함 (알려진 한계 참고).
        fdr_df = fdr.StockListing('KRX-DESC')[['Code', 'Industry']]
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

    def get_sector_metrics(self, base_date: date) -> pd.DataFrame:
        """
        pykrx를 활용하여 KOSPI 전 종목의 기간별 수익률과 거래대금을 수집한 뒤,
        섹터(Industry)별로 그룹화하여 반환합니다.
        """
        from pykrx import stock
        from dateutil.relativedelta import relativedelta
        import pandas as pd

        date_str = self._get_nearest_past_bday(base_date)
        date_1m = self._get_nearest_past_bday(base_date - relativedelta(months=1))
        date_6m = self._get_nearest_past_bday(base_date - relativedelta(months=6))
        date_1y = self._get_nearest_past_bday(base_date - relativedelta(years=1))
        
        self.logger.info("실전 데이터 조달: pykrx 기간별 수익률/거래대금 API 호출 중...")
        
        # 1. 기간별 등락률 및 거래대금 (KOSPI 전 종목)
        df_1m = self._fetch_with_retry(
            lambda: stock.get_market_price_change(date_1m, date_str, market="KOSPI"), label=f"1개월 등락률({date_str})"
        ).reset_index()
        time.sleep(1.0)
        df_6m = self._fetch_with_retry(
            lambda: stock.get_market_price_change(date_6m, date_str, market="KOSPI"), label=f"6개월 등락률({date_str})"
        ).reset_index()
        time.sleep(1.0)
        df_1y = self._fetch_with_retry(
            lambda: stock.get_market_price_change(date_1y, date_str, market="KOSPI"), label=f"1년 등락률({date_str})"
        ).reset_index()
        
        # 컬럼명 정리 및 등락률 단위 변환 (% -> 소수점)
        df_1m = df_1m[['티커', '등락률', '거래대금']].rename(columns={'티커': 'ticker', '등락률': 'return_1m', '거래대금': 'vol_1m'})
        df_1m['return_1m'] = df_1m['return_1m'] / 100.0
        
        df_6m = df_6m[['티커', '등락률']].rename(columns={'티커': 'ticker', '등락률': 'return_6m'})
        df_6m['return_6m'] = df_6m['return_6m'] / 100.0
        
        df_1y = df_1y[['티커', '거래대금']].rename(columns={'티커': 'ticker', '거래대금': 'vol_1y'})
        
        # 2. 유니버스(섹터 정보)와 병합
        # (미리 구현된 get_kospi_universe 메서드를 통해 티커-섹터 매핑을 가져옵니다)
        universe = self.get_kospi_universe(base_date)
        df = pd.merge(universe, df_1m, on='ticker', how='left')
        df = pd.merge(df, df_6m, on='ticker', how='left')
        df = pd.merge(df, df_1y, on='ticker', how='left')
        
        # 3. 섹터별 집계 (수익률은 시가총액가중 평균, 거래대금은 단순 합산)
        # 과거엔 종목 단순평균(mean)을 썼는데, 이러면 저유동성 소형주 몇 개가 섹터 전체의
        # "소외도" 신호를 왜곡할 수 있어 시가총액가중으로 교체함 (screening_criteria.md 설계 원칙과 일치).
        # 결측 수익률 종목은 분자(가중합)에서 자연히 빠지고, 분모(가중치 합)에서도 함께 제외되도록
        # market_cap을 NaN으로 마스킹한 보조 컬럼으로 가중평균을 벡터화 연산한다.
        market_vol_1m = df['vol_1m'].sum()
        market_vol_1y = df['vol_1y'].sum()

        df['_w_return_1m'] = df['return_1m'] * df['market_cap']
        df['_w_return_6m'] = df['return_6m'] * df['market_cap']
        df['_w_cap_1m'] = df['market_cap'].where(df['return_1m'].notna())
        df['_w_cap_6m'] = df['market_cap'].where(df['return_6m'].notna())

        sector_group = df.groupby('sector').agg(
            _wsum_return_1m=('_w_return_1m', 'sum'),
            _wsum_return_6m=('_w_return_6m', 'sum'),
            _wcap_1m=('_w_cap_1m', 'sum'),
            _wcap_6m=('_w_cap_6m', 'sum'),
            sector_vol_1m=('vol_1m', 'sum'),
            sector_vol_1y=('vol_1y', 'sum'),
        ).reset_index()

        sector_group['return_1m'] = sector_group['_wsum_return_1m'] / sector_group['_wcap_1m']
        sector_group['return_6m'] = sector_group['_wsum_return_6m'] / sector_group['_wcap_6m']

        # 4. 시장 전체 대비 거래대금 비중 산출
        sector_group['vol_prop_1m'] = sector_group['sector_vol_1m'] / market_vol_1m
        sector_group['vol_prop_1y'] = sector_group['sector_vol_1y'] / market_vol_1y
        
        return sector_group[['sector', 'return_1m', 'return_6m', 'vol_prop_1m', 'vol_prop_1y']]

    def get_financial_statements(self, ticker: str, year: int, report_code: str = '11011', fs_div: str = 'CFS') -> Optional[pd.DataFrame]:
        """재시도(Retry) 및 캐시 무효화가 적용된 DART 데이터 로더"""
        cache_file = self.cache_dir / f"dart_{ticker}_{year}_{report_code}_{fs_div}.csv"
        # "데이터 없음"(개별재무제표만 있는 회사의 CFS 요청 등 정상적인 무응답)도 결과로 캐싱하는
        # 마커 파일. 🚨 [수정] 과거엔 이 케이스를 캐싱하지 않아, 같은 조합을 요청할 때마다
        # (분기가 겹치는 다른 base_date에서, 혹은 재실행할 때마다) 매번 실시간 API를 낭비 호출했음
        # — 실제로 이 버그 때문에 DART 일일 한도가 예상보다 훨씬 빨리 소진되는 사례가 발생함.
        empty_marker = self.cache_dir / f"dart_{ticker}_{year}_{report_code}_{fs_div}.empty"

        # 확정 공시된 재무제표는 사실상 불변이므로 dart_cache_days(기본 3650일)를 별도 적용
        if self.use_cache and self._is_cache_valid(cache_file, cache_days=self.dart_cache_days):
            return pd.read_csv(cache_file)
        # "없음" 마커는 일반 cache_days(기본 30일)만 적용 — 아주 최근 분기는 아직 미공시일 뿐일
        # 수 있어, 영구 캐싱(dart_cache_days)하면 나중에 실제로 공시돼도 계속 없다고 오판할 위험이 있음
        if self.use_cache and self._is_cache_valid(empty_marker):
            return None

        for attempt in range(self.dart_retries):
            try:
                # 일반 Exception이 아닌 RuntimeError로 발생시켜 명확히 구분
                if self.dart_call_count >= self.dart_daily_limit:
                    raise RuntimeError(f"[Rate Limit] DART API 일일 호출 한도({self.dart_daily_limit}회)에 도달하여 스크리닝을 중단합니다.")

                self.dart_call_count += 1
                self._persist_dart_call_count()

                fs_df = self.dart.finstate_all(ticker, year, reprt_code=report_code, fs_div=fs_div)
                if fs_df is not None and not fs_df.empty:
                    if self.use_cache:
                        fs_df.to_csv(cache_file, index=False, encoding='utf-8-sig')
                    return fs_df
                if self.use_cache:
                    empty_marker.touch()
                break

            except RuntimeError as limit_err:
                # 🚨 Rate Limit 에러는 재시도하지 않고 즉시 메인 프로그램으로 에러를 던짐
                raise limit_err
                
            except Exception as e:
                # 기타 네트워크 에러 등은 기존처럼 3회 재시도
                if attempt == self.dart_retries - 1:
                    warnings.warn(f"[DART API 최종 실패] {ticker} ({fs_div}): {e}")
                    return None
                time.sleep(1)
        return None

    def _extract_amount(self, row, target_df: pd.DataFrame, prefer_cumulative: bool) -> float:
        """
        한 행(row)에서 금액을 파싱합니다.
        prefer_cumulative=True(IS/CF 계정)면 분기 누적치(thstrm_add_amount)를 우선 사용하고,
        없으면 기본 컬럼(thstrm_amount)으로 폴백합니다.
        """
        amount = float('nan')

        if prefer_cumulative and 'thstrm_add_amount' in target_df.columns and pd.notna(row.get('thstrm_add_amount')):
            add_amt_str = str(row['thstrm_add_amount']).replace(',', '').strip()
            try:
                amount = float(add_amt_str)
            except ValueError:
                pass

        if pd.isna(amount):
            amt_str = str(row.get('thstrm_amount', '')).replace(',', '').strip()
            try:
                amount = float(amt_str)
            except ValueError:
                pass

        return amount

    def _match_account(self, df_subset: pd.DataFrame, target_df: pd.DataFrame, ids=None, names=None,
                        prefer_cumulative: bool = True) -> float:
        """
        df_subset(특정 sj_div로 이미 필터링된 행들)에서 계정 하나를 찾아 금액을 반환합니다.
        정확한 account_id 매치를 이름 부분 문자열 매치보다 항상 우선시합니다 — DART 원본 파일 내
        행 등장 순서에 결과가 좌우되지 않도록 하기 위함입니다(자세한 배경은 ACCOUNT_MAPPING의
        interest_expense 주석 참고).
        """
        ids = ids or []
        names = names or []

        # 1차: 정확한 account_id 매치
        if ids:
            id_matched = df_subset[df_subset['account_id'].astype(str).str.strip().isin(ids)]
            for _, row in id_matched.iterrows():
                amount = self._extract_amount(row, target_df, prefer_cumulative)
                if pd.notna(amount):
                    return amount

        # 2차: 계정명 부분 문자열 매치
        if names:
            for _, row in df_subset.iterrows():
                acc_nm = str(row.get('account_nm', '')).strip()
                if any(name in acc_nm for name in names):
                    amount = self._extract_amount(row, target_df, prefer_cumulative)
                    if pd.notna(amount):
                        return amount

        return float('nan')

    def _sum_matching_accounts(self, df_subset: pd.DataFrame, target_df: pd.DataFrame, ids=None, names=None) -> float:
        """
        df_subset 내에서 ids 또는 names에 매치되는 '모든' 행의 금액을 합산합니다.
        차입금처럼 유동/비유동/사채 등 여러 행으로 쪼개져 보고되는 계정을 합산할 때 사용합니다
        (단일 최고 매치만 찾는 _match_account와 달리 전부 더함). 매치되는 행이 하나도 없으면
        0.0을 반환합니다(무차입으로 간주 — NaN을 반환하면 부채 없는 우량 기업이 오히려
        ROIC_NOT_COMPUTABLE로 구제 처리되어 버리는 부작용이 생김).
        """
        ids = set(ids or [])
        names = names or []
        total = 0.0

        for _, row in df_subset.iterrows():
            acc_id = str(row.get('account_id', '')).strip()
            acc_nm = str(row.get('account_nm', '')).strip()

            if acc_id in ids or any(n in acc_nm for n in names):
                amount = self._extract_amount(row, target_df, prefer_cumulative=False)
                if pd.notna(amount):
                    total += amount

        return total

    def parse_standardized_financials(self, ticker: str, year: int, report_code: str = '11011', base_date: Optional[date] = None) -> Dict[str, float]:
        """
        base_date가 제공될 경우 공시 시차(Disclosure Lag)를 검증하여, 
        해당 일자 시점에 공시되지 않은 데이터는 무효(NaN) 처리합니다.
        """
        standard_metrics = {key: float('nan') for key in self.ACCOUNT_MAPPING.keys()}

        # 1. 연결재무제표(CFS) 우선 요청
        target_df = self.get_financial_statements(ticker, year, report_code, fs_div='CFS')
        
        # 2. 연결재무제표가 없거나 비어있으면 별도재무제표(OFS) 요청
        if target_df is None or target_df.empty:
            target_df = self.get_financial_statements(ticker, year, report_code, fs_div='OFS')

        if target_df is None or target_df.empty or 'thstrm_amount' not in target_df.columns:
            return standard_metrics

        # ---------------------------------------------------------
        # [핵심 로직] 공시 시차 검증 (Look-ahead bias 방지)
        # 이 프로젝트에서 미래 데이터 유입을 막는 유일한 안전장치이므로 fail-closed 원칙을 적용한다:
        # 공시일을 확인할 수 없는 경우(컬럼 누락, 파싱 실패 등) 검증을 조용히 건너뛰고 데이터를
        # 그대로 쓰는 대신, 안전하게 실패(NaN)시켜 해당 종목이 구제 없이 계산 불가 처리되도록 한다.
        # 이유: 검증 스킵으로 인한 미래 데이터 오염이 종목 하나 계산 누락보다 훨씬 치명적임.
        # ---------------------------------------------------------
        if base_date is not None:
            if 'rcept_no' not in target_df.columns:
                self.logger.error(
                    f"[공시 시차 검증 불가] {ticker}의 {year}년 {report_code} 보고서에 rcept_no "
                    f"컬럼이 없어 공시 시점을 확인할 수 없습니다. 미래참조 위험을 피하기 위해 "
                    f"안전하게 실패(NaN) 처리합니다."
                )
                return standard_metrics

            try:
                # DART rcept_no의 앞 8자리는 접수일자(YYYYMMDD)
                rcept_no = str(target_df['rcept_no'].iloc[0])
                rcept_dt = datetime.strptime(rcept_no[:8], "%Y%m%d").date()
            except Exception as e:
                self.logger.error(
                    f"[공시일 파싱 오류] {ticker}: {e} — 공시 시점을 확인할 수 없어 "
                    f"미래참조 위험을 피하기 위해 안전하게 실패(NaN) 처리합니다."
                )
                return standard_metrics

            if rcept_dt > base_date:
                self.logger.warning(
                    f"[미래참조 방지] {ticker}의 {year}년 {report_code} 보고서는 "
                    f"{base_date} 시점에 미공시 상태입니다. (실제 공시일: {rcept_dt})"
                )
                return standard_metrics # 공시 전이므로 빈 껍데기(NaN) 반환

        # 3. 재무제표 종류(sj_div) 및 계정 매핑
        # total_borrowings(합산 필요)과 interest_expense(다단계 폴백 필요)는 아래에서 별도 처리
        for standard_key, rules in self.ACCOUNT_MAPPING.items():
            if standard_key in ('total_borrowings', 'interest_expense'):
                continue

            # sj_div (BS: 재무상태표, IS: 손익계산서, CF: 현금흐름표) 필터링
            sj_filtered = target_df[target_df['sj_div'].astype(str).str.contains(rules["sj"], na=False)]
            standard_metrics[standard_key] = self._match_account(
                sj_filtered, target_df,
                ids=rules.get("ids"), names=rules.get("names"),
                prefer_cumulative=(rules["sj"] in ['IS', 'CF'])
            )

        # ---------------------------------------------------------
        # [특수 처리 1] 총차입금: 유동/비유동/사채 등 여러 행을 전부 합산
        # ---------------------------------------------------------
        borrow_rules = self.ACCOUNT_MAPPING['total_borrowings']
        bs_filtered = target_df[target_df['sj_div'].astype(str).str.contains(borrow_rules["sj"], na=False)]
        standard_metrics['total_borrowings'] = self._sum_matching_accounts(
            bs_filtered, target_df, ids=borrow_rules.get("ids"), names=borrow_rules.get("names")
        )

        # ---------------------------------------------------------
        # [특수 처리 2] 이자비용: ①정확계정 → ②P&L "이자비용" → ③CF 이자지급 조정 라인
        # → ④광의 금융비용(최후 수단) 순으로 시도
        # ---------------------------------------------------------
        int_rules = self.ACCOUNT_MAPPING['interest_expense']
        is_filtered = target_df[target_df['sj_div'].astype(str).str.contains(int_rules["sj"], na=False)]

        standard_metrics['interest_expense'] = self._match_account(
            is_filtered, target_df, ids=int_rules.get("ids"), names=int_rules.get("names"),
            prefer_cumulative=True
        )

        if pd.isna(standard_metrics['interest_expense']):
            cf_filtered = target_df[target_df['sj_div'].astype(str) == 'CF']
            standard_metrics['interest_expense'] = self._match_account(
                cf_filtered, target_df, ids=int_rules.get("cf_ids"), prefer_cumulative=True
            )

        if pd.isna(standard_metrics['interest_expense']):
            standard_metrics['interest_expense'] = self._match_account(
                is_filtered, target_df, names=int_rules.get("fallback_names"), prefer_cumulative=True
            )

        return standard_metrics

    def get_historical_ohlcv(self, ticker: str, start_date: date, end_date: date) -> Optional[pd.DataFrame]:
        """
        과거 시계열 주가 및 거래량 데이터 (Stage 1 소외도 분석용)
        """
        # 🚨 수정: 시작일과 종료일 모두 과거 영업일로 단단히 고정
        start_str = self._get_nearest_past_bday(start_date)
        end_str = self._get_nearest_past_bday(end_date)

        date_str = f"{start_str}_{end_str}"
        cache_file = self.cache_dir / f"ohlcv_{ticker}_{date_str}.csv"
        
        if self.use_cache and self._is_cache_valid(cache_file):
            return pd.read_csv(cache_file, parse_dates=['Date'], index_col='Date', encoding='utf-8-sig')
            
        try:
            df = fdr.DataReader(ticker, start_str, end_str)
            if not df.empty and self.use_cache:
                df.to_csv(cache_file, encoding='utf-8-sig')
            return df
        except Exception as e:
            warnings.warn(f"[FDR 시계열 호출 실패] {ticker}: {e}")
            return None

    def get_isolated_quarterly_financials(self, ticker: str, year: int, quarter: int, base_date: Optional[date] = None) -> Dict[str, float]:
        """
        분기 단독 재무제표 추출 래퍼 (누적 데이터 차분 처리, base_date를 전달하여 공시 시차 검증)
        """
        if quarter == 1:
            return self.parse_standardized_financials(ticker, year, '11013', base_date)
            
        elif quarter == 2:
            q2_cum = self.parse_standardized_financials(ticker, year, '11012', base_date)
            q1_cum = self.parse_standardized_financials(ticker, year, '11013', base_date)

            isolated = {}
            for k in q2_cum:
                sj_type = self.ACCOUNT_MAPPING[k]["sj"]
                
                if sj_type == "BS":
                    # 재무상태표는 누적 개념이 없으므로 해당 분기 잔액 그대로 사용
                    isolated[k] = q2_cum[k]
                else:
                    # 손익/현금흐름은 (해당 분기 누적 - 직전 분기 누적) 차분 연산
                    isolated[k] = (q2_cum[k] - q1_cum[k]) if pd.notna(q2_cum[k]) and pd.notna(q1_cum[k]) else float('nan')
            return isolated
                        
        elif quarter == 3:
            q3_cum = self.parse_standardized_financials(ticker, year, '11014', base_date)
            q2_cum = self.parse_standardized_financials(ticker, year, '11012', base_date)

            isolated = {}
            for k in q3_cum:
                sj_type = self.ACCOUNT_MAPPING[k]["sj"]
                
                if sj_type == "BS":
                    # 재무상태표는 누적 개념이 없으므로 해당 분기 잔액 그대로 사용
                    isolated[k] = q3_cum[k]
                else:
                    # 손익/현금흐름은 (해당 분기 누적 - 직전 분기 누적) 차분 연산
                    isolated[k] = (q3_cum[k] - q2_cum[k]) if pd.notna(q3_cum[k]) and pd.notna(q2_cum[k]) else float('nan')
            return isolated
                   
        elif quarter == 4:
            annual = self.parse_standardized_financials(ticker, year, '11011', base_date)
            q3_cum = self.parse_standardized_financials(ticker, year, '11014', base_date)
            
            isolated = {}
            for k in annual:
                sj_type = self.ACCOUNT_MAPPING[k]["sj"]
                
                if sj_type == "BS":
                    # 재무상태표는 누적 개념이 없으므로 해당 분기 잔액 그대로 사용
                    isolated[k] = annual[k]
                else:
                    # 손익/현금흐름은 (해당 분기 누적 - 직전 분기 누적) 차분 연산
                    isolated[k] = (annual[k] - q3_cum[k]) if pd.notna(annual[k]) and pd.notna(q3_cum[k]) else float('nan')
            return isolated
            
        else:
            raise ValueError("quarter는 1에서 4 사이의 정수여야 합니다.")

    def get_ttm_financials(self, ticker: str, base_date: date) -> Dict[str, float]:
        """
        base_date 기준으로 공시가 완료된 최근 4개 분기의 재무 데이터를 조회하여 TTM을 계산합니다.
        - IS/CF 계정 (Flow): 4개 분기 합산
        - BS 계정 (Stock): self.ttm_denominator 설정에 따라 최근 분기말 스냅샷("latest_snapshot",
          기본값) 또는 4개 분기 평균("avg_4q")
        """
        ttm_metrics = {key: float('nan') for key in self.ACCOUNT_MAPPING.keys()}
        
        valid_quarters = []
        target_year = base_date.year
        # 현재 날짜 기준 대략적인 해당 분기 계산
        target_quarter = (base_date.month - 1) // 3 + 1
        
        # 최대 8개 분기(2년)까지 거슬러 올라가며 공시된 4개 분기를 찾음
        for _ in range(8):
            q_data = self.get_isolated_quarterly_financials(ticker, target_year, target_quarter, base_date)
            
            # 유효한 데이터(모두 NaN이 아닌 경우)인지 확인
            if not all(pd.isna(v) for v in q_data.values()):
                valid_quarters.append(q_data)
                
            if len(valid_quarters) == 4:
                break
                
            # 이전 분기로 이동
            target_quarter -= 1
            if target_quarter == 0:
                target_quarter = 4
                target_year -= 1

        # 4개 분기 데이터를 모두 확보하지 못한 경우 (상장된지 1년 미만이거나 공시 누락 등)
        if len(valid_quarters) < 4:
            self.logger.warning(f"[TTM 계산 불가] {ticker}: {base_date} 기준 유효한 4개 분기 데이터를 찾지 못했습니다.")
            return ttm_metrics

        # 가장 최근 분기 (인덱스 0)
        latest_q = valid_quarters[0]
        
        for key, rules in self.ACCOUNT_MAPPING.items():
            if rules["sj"] == "BS":
                if self.ttm_denominator == "avg_4q":
                    # 4개 분기 평균: 하나라도 결측치면 왜곡 방지를 위해 NaN 유지(Flow 항목과 동일 원칙)
                    val_list = [q.get(key, float('nan')) for q in valid_quarters]
                    if any(pd.isna(v) for v in val_list):
                        ttm_metrics[key] = float('nan')
                    else:
                        ttm_metrics[key] = sum(val_list) / len(val_list)
                else:
                    # "latest_snapshot"(기본값): 가장 최근 분기말 잔액 스냅샷 사용
                    ttm_metrics[key] = latest_q.get(key, float('nan'))
            else:
                # 손익계산서/현금흐름표(Flow)는 4개 분기 합산
                # 하나라도 결측치가 있으면 합산값의 왜곡을 막기 위해 NaN 유지
                val_list = [q.get(key, float('nan')) for q in valid_quarters]
                if any(pd.isna(v) for v in val_list):
                    ttm_metrics[key] = float('nan')
                else:
                    ttm_metrics[key] = sum(val_list)

        return ttm_metrics

    def get_annual_financials(self, ticker: str, base_date: date) -> Dict[str, float]:
        """
        base_date 기준으로 공시가 완료된 가장 최근의 사업보고서(연간) 데이터를 반환합니다.
        """
        annual_metrics = {key: float('nan') for key in self.ACCOUNT_MAPPING.keys()}
        
        # 최대 3년 전까지 거슬러 올라감
        target_year = base_date.year
        for y in range(target_year, target_year - 3, -1):
            data = self.parse_standardized_financials(ticker, y, '11011', base_date)
            # 유효한 데이터가 존재하면 즉시 반환 (가장 최근 확정치)
            if not all(pd.isna(v) for v in data.values()):
                return data
                
        self.logger.warning(f"[연간 데이터 불가] {ticker}: {base_date} 기준 최근 3년 내 공시된 사업보고서를 찾지 못했습니다.")
        return annual_metrics

    def get_quarterly_op_margin_series(self, ticker: str, base_date: date, n_quarters: int = 8) -> list:
        """
        base_date 기준으로 최근 n개 분기의 '단독' 영업이익률 시계열을 조회합니다.
        - 결측치나 공시 전 데이터는 제외하지 않고 float('nan')으로 채워서 반환합니다.
        """
        op_margin_series = []
        target_year = base_date.year
        # 현재 날짜 기준 대략적인 분기 계산
        target_quarter = (base_date.month - 1) // 3 + 1
        
        for _ in range(n_quarters):
            # 분기 단독 데이터 추출 (공시 시차 검증 포함)
            q_data = self.get_isolated_quarterly_financials(ticker, target_year, target_quarter, base_date)
            
            op_inc = q_data.get('operating_income', float('nan'))
            rev = q_data.get('revenue', float('nan'))
            
            # 매출액이 존재하고 0이 아닌 경우에만 비율 계산
            if pd.notna(op_inc) and pd.notna(rev) and rev != 0:
                op_margin_series.append(float(op_inc / rev))
            else:
                op_margin_series.append(float('nan'))
                
            # 이전 분기로 이동
            target_quarter -= 1
            if target_quarter == 0:
                target_quarter = 4
                target_year -= 1
                
        return op_margin_series

    def get_quarterly_financials_series(self, ticker: str, base_date: date, n_quarters: int = 6) -> list:
        """
        base_date 기준으로 공시가 완료된 가장 최신 분기를 찾은 후, 해당 시점부터 과거 n개 분기의 '단독' 재무제표 딕셔너리 시계열을 반환합니다.
        """
        financials_series = []
        target_year = base_date.year
        target_quarter = (base_date.month - 1) // 3 + 1

        # 1. 공시 시차(Lag)를 고려하여 데이터가 존재하는 가장 최신 분기(t=0) 찾기
        found_latest = False
        max_lag_search = 3 # 최대 3개 분기 과거까지 탐색
        
        for _ in range(max_lag_search):
            q_data = self.get_isolated_quarterly_financials(ticker, target_year, target_quarter, base_date)
            # 매출액 데이터가 존재한다면 공시가 완료된 분기로 판단
            if q_data and pd.notna(q_data.get('revenue', float('nan'))):
                found_latest = True
                break
            
            # 데이터가 없으면 한 분기 전으로 이동
            target_quarter -= 1
            if target_quarter == 0:
                target_quarter = 4
                target_year -= 1
                
        if not found_latest:
            self.logger.warning(f"[{ticker}] 최근 공시 데이터를 찾을 수 없습니다.")
            return []

        # 2. 확정된 t=0 기점으로부터 n_quarters 만큼 시계열 수집
        for _ in range(n_quarters):
            q_data = self.get_isolated_quarterly_financials(ticker, target_year, target_quarter, base_date)
            financials_series.append(q_data)
            
            target_quarter -= 1
            if target_quarter == 0:
                target_quarter = 4
                target_year -= 1
                
        return financials_series

    def extract_account_value(self, df, account_info, reprt_code):
        """
        확장된 키워드를 기반으로 계정 값을 안전하게 추출하고,
        사업보고서(Q4)의 단독값 누락을 방어합니다.
        """
        matched_rows = df[
            (df['sj_div'].isin(account_info['sj'])) & 
            (df['account_nm'].str.contains('|'.join(account_info['names']), na=False))
        ]
        
        if matched_rows.empty:
            return np.nan
            
        row = matched_rows.iloc[0] # 가장 먼저 매칭된 표준 계정 사용
        
        # =====================================================================
        # 🚨 [여기 추가!] 4분기(사업보고서) 방어 로직
        # 단독값(add_amount)을 꺼내기 전에, 사업보고서인데 단독값이 비어있는지 먼저 검사
        # =====================================================================
        if reprt_code == '11011':  # 4분기 사업보고서인 경우
            if 'thstrm_add_amount' not in row or pd.isna(row['thstrm_add_amount']) or str(row['thstrm_add_amount']).strip() == '':
                # 4분기 단독값이 없으면, 아래의 기존 로직으로 내려가 누적치(amount)를 
                # 억지로 쓰지 못하도록 여기서 원천 차단하고 결측(NaN) 처리합니다.
                return float('nan') 
        # =====================================================================
        
        # 2. 값 파싱 시도 (기존에 작성해두신 분기 단독값 판별 try-except 로직)
        try:
            # thstrm_add_amount 컬럼이 존재하고 값이 있으면 우선 사용
            if 'thstrm_add_amount' in row and pd.notna(row['thstrm_add_amount']):
                val = str(row['thstrm_add_amount']).replace(',', '').strip()
                if val:
                    return float(val)
                    
            # 위 방어 로직을 통과한 1~3분기 보고서 중 add_amount가 없는 경우 기본값 캐스팅
            val = str(row['thstrm_amount']).replace(',', '').strip()
            return float(val) if val else float('nan')
            
        except Exception as e:
            return float('nan')

    def get_market_fundamental_cross_section(self, base_date: date) -> pd.DataFrame:
        """
        base_date 기준 KOSPI 전 종목의 펀더멘털 지표(PBR, BPS 등) 스냅샷을 조회합니다.
        (pykrx 기시산출값 활용)
        """
        date_str = self._get_nearest_past_bday(base_date)
        cache_file = self.cache_dir / f"fundamentals_{date_str}.csv"
        
        if self.use_cache and self._is_cache_valid(cache_file):
            return pd.read_csv(cache_file, dtype={'ticker': str}, encoding='utf-8-sig')

        try:
            # pykrx를 통한 펀더멘털 데이터 수집 (일시적 네트워크 오류는 재시도로 흡수)
            df = self._fetch_with_retry(
                lambda: stock.get_market_fundamental(date_str, market="KOSPI"), label=f"펀더멘털({date_str})"
            )
            if df.empty:
                return pd.DataFrame()
                
            df = df.reset_index()
            # 컬럼명 영문 표준화 (필요한 컬럼만 추출)
            df = df.rename(columns={'티커': 'ticker', 'BPS': 'bps', 'PBR': 'pbr', 'PER': 'per'})
            df = df[['ticker', 'bps', 'pbr', 'per']]
            
            if self.use_cache:
                df.to_csv(cache_file, index=False, encoding='utf-8-sig')
                
            return df
        except Exception as e:
            self.logger.error(f"[FDR/pykrx 펀더멘털 호출 실패] {date_str}: {e}")
            return pd.DataFrame()

    def get_quarterly_rebalance_dates(self, start_year: int, end_year: int) -> list[date]:
        """
        달력 기준 분기말 날짜를 생성한 뒤, 내부 헬퍼 함수를 통과시켜
        KOSPI 실제 영업일 캘린더로 완벽하게 치환하여 반환합니다.
        """
        start_str = f"{start_year}-01-01"
        end_str = f"{end_year}-12-31"
        
        # 기계적인 달력 기준 분기말 (예: 2019-03-31, 2019-06-30 ...)
        quarter_ends = pd.date_range(start=start_str, end=end_str, freq="QE")
        
        valid_b_dates = []
        self.logger.info(f"🗓️ KOSPI 실제 영업일 분기말 캘린더 추출 중 ({start_year}~{end_year})...")
        
        for d in quarter_ends:
            # 💡 헬퍼 함수를 재사용하여 가장 가까운 과거 영업일 문자열(YYYYMMDD) 획득
            valid_bday_str = self._get_nearest_past_bday(d.date())
            
            # 파이프라인과 엔진이 사용할 수 있도록 다시 date 객체로 변환
            valid_bday_date = datetime.strptime(valid_bday_str, "%Y%m%d").date()
            valid_b_dates.append(valid_bday_date)
            
        return valid_b_dates