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
    def __init__(self, use_cache: bool = True, cache_days: int = 30):
        load_dotenv()
        self.dart_key = os.getenv("DART_API_KEY")
        self.krx_key = os.getenv("KRX_API_KEY")
        
        if not self.dart_key:
            raise ValueError("[오류] DART_API_KEY가 .env 파일에 없습니다.")
            
        self.dart = OpenDartReader(self.dart_key)
        self.use_cache = use_cache
        self.cache_days = cache_days

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
            self.dart_retries = 3

        # config 로드 블록 하단에 추가 (DART API 일일 호출량 관리)
        self.dart_daily_limit = self.endpoints.get("DART", {}).get("daily_limit", 9500)
        self.dart_call_count = 0

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
            "interest_expense": { # 이자비용
                "sj": "IS",
                "ids": ["ifrs-full_InterestExpense", "dart_InterestExpense"],
                "names": ["금융원가", "금융비용", "이자비용"]
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

        date_str = base_date.strftime("%Y%m%d")
        date_1m = (base_date - relativedelta(months=1)).strftime("%Y%m%d")
        date_6m = (base_date - relativedelta(months=6)).strftime("%Y%m%d")
        date_1y = (base_date - relativedelta(years=1)).strftime("%Y%m%d")
        
        self.logger.info("실전 데이터 조달: pykrx 기간별 수익률/거래대금 API 호출 중...")
        
        # 1. 기간별 등락률 및 거래대금 (KOSPI 전 종목)
        df_1m = stock.get_market_price_change(date_1m, date_str, market="KOSPI").reset_index()
        df_6m = stock.get_market_price_change(date_6m, date_str, market="KOSPI").reset_index()
        df_1y = stock.get_market_price_change(date_1y, date_str, market="KOSPI").reset_index()
        
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
        
        # 3. 섹터별 집계 (수익률은 동일가중 평균, 거래대금은 단순 합산)
        market_vol_1m = df['vol_1m'].sum()
        market_vol_1y = df['vol_1y'].sum()
        
        sector_group = df.groupby('sector').agg(
            return_1m=('return_1m', 'mean'),
            return_6m=('return_6m', 'mean'),
            sector_vol_1m=('vol_1m', 'sum'),
            sector_vol_1y=('vol_1y', 'sum')
        ).reset_index()
        
        # 4. 시장 전체 대비 거래대금 비중 산출
        sector_group['vol_prop_1m'] = sector_group['sector_vol_1m'] / market_vol_1m
        sector_group['vol_prop_1y'] = sector_group['sector_vol_1y'] / market_vol_1y
        
        return sector_group[['sector', 'return_1m', 'return_6m', 'vol_prop_1m', 'vol_prop_1y']]

    def get_financial_statements(self, ticker: str, year: int, report_code: str = '11011', fs_div: str = 'CFS') -> Optional[pd.DataFrame]:
        """재시도(Retry) 및 캐시 무효화가 적용된 DART 데이터 로더"""
        cache_file = self.cache_dir / f"dart_{ticker}_{year}_{report_code}_{fs_div}.csv"
        
        if self.use_cache and self._is_cache_valid(cache_file):
            return pd.read_csv(cache_file)

        for attempt in range(self.dart_retries):
            try:
                # 일반 Exception이 아닌 RuntimeError로 발생시켜 명확히 구분
                if self.dart_call_count >= self.dart_daily_limit:
                    raise RuntimeError(f"[Rate Limit] DART API 일일 호출 한도({self.dart_daily_limit}회)에 도달하여 스크리닝을 중단합니다.")
                
                self.dart_call_count += 1

                fs_df = self.dart.finstate_all(ticker, year, reprt_code=report_code, fs_div=fs_div)
                if fs_df is not None and not fs_df.empty:
                    if self.use_cache:
                        fs_df.to_csv(cache_file, index=False, encoding='utf-8-sig')
                    return fs_df
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
        # ---------------------------------------------------------
        if base_date is not None and 'rcept_no' in target_df.columns:
            try:
                # DART rcept_no의 앞 8자리는 접수일자(YYYYMMDD)
                rcept_no = str(target_df['rcept_no'].iloc[0])
                rcept_dt = datetime.strptime(rcept_no[:8], "%Y%m%d").date()
                
                if rcept_dt > base_date:
                    self.logger.warning(
                        f"[미래참조 방지] {ticker}의 {year}년 {report_code} 보고서는 "
                        f"{base_date} 시점에 미공시 상태입니다. (실제 공시일: {rcept_dt})"
                    )
                    return standard_metrics # 공시 전이므로 빈 껍데기(NaN) 반환
            except Exception as e:
                self.logger.error(f"[공시일 파싱 오류] {ticker}: {e}")
        
        # 3. 재무제표 종류(sj_div) 및 계정 매핑
        for standard_key, rules in self.ACCOUNT_MAPPING.items():
            # sj_div (BS: 재무상태표, IS: 손익계산서, CF: 현금흐름표) 필터링
            sj_filtered = target_df[target_df['sj_div'].str.contains(rules["sj"], na=False)]
            
            for _, row in sj_filtered.iterrows():
                acc_id = str(row.get('account_id', '')).strip()
                acc_nm = str(row.get('account_nm', '')).strip()

                # ---------------------------------------------------
                # [수정된 파싱 로직] isdigit() 대신 try-except float 캐스팅 사용
                # ---------------------------------------------------
                amount = float('nan')
                
                # 1. IS/CF 계정이면서 누적치 컬럼이 존재하는 경우 (누적치 우선)
                # [개선] 컬럼 존재 여부 사전 확인 (KeyError 방지)
                if rules["sj"] in ['IS', 'CF'] and 'thstrm_add_amount' in target_df.columns:
                    if pd.notna(row['thstrm_add_amount']):
                        add_amt_str = str(row['thstrm_add_amount']).replace(',', '').strip()
                        try:
                            amount = float(add_amt_str)
                        except ValueError:
                            pass
                
                # 2. BS 계정이거나, IS/CF지만 누적치 컬럼이 없는 경우 기본 컬럼 사용
                if pd.isna(amount):
                    amt_str = str(row.get('thstrm_amount', '')).replace(',', '').strip()
                    try:
                        amount = float(amt_str)
                    except ValueError:
                        pass

                if pd.isna(amount):
                    continue
                # ---------------------------------------------------
            
                if acc_id in rules["ids"] or any(name in acc_nm for name in rules["names"]):
                    standard_metrics[standard_key] = amount
                    break

        return standard_metrics

    def get_historical_ohlcv(self, ticker: str, start_date: date, end_date: date) -> Optional[pd.DataFrame]:
        """
        과거 시계열 주가 및 거래량 데이터 (Stage 1 소외도 분석용)
        """
        date_str = f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
        cache_file = self.cache_dir / f"ohlcv_{ticker}_{date_str}.csv"
        
        if self.use_cache and self._is_cache_valid(cache_file):
            return pd.read_csv(cache_file, parse_dates=['Date'], index_col='Date', encoding='utf-8-sig')
            
        try:
            df = fdr.DataReader(ticker, start_date, end_date)
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
        - BS 계정 (Stock): 가장 최근 분기말 잔액 스냅샷
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
                # 재무상태표(Stock)는 가장 최근 분기말 잔액 스냅샷 사용
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
        date_str = base_date.strftime("%Y%m%d")
        cache_file = self.cache_dir / f"fundamentals_{date_str}.csv"
        
        if self.use_cache and self._is_cache_valid(cache_file):
            return pd.read_csv(cache_file, dtype={'ticker': str}, encoding='utf-8-sig')

        try:
            # pykrx를 통한 펀더멘털 데이터 수집
            df = stock.get_market_fundamental(date_str, market="KOSPI")
            if df.empty:
                return pd.DataFrame()
                
            df = df.reset_index()
            # 컬럼명 영문 표준화 (필요한 컬럼만 추출)
            df = df.rename(columns={'티커': 'ticker', 'BPS': 'bps', 'PBR': 'pbr'})
            df = df[['ticker', 'bps', 'pbr']]
            
            if self.use_cache:
                df.to_csv(cache_file, index=False, encoding='utf-8-sig')
                
            return df
        except Exception as e:
            self.logger.error(f"[FDR/pykrx 펀더멘털 호출 실패] {date_str}: {e}")
            return pd.DataFrame()