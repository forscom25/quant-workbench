# stock_screener 아키텍처 노트

> 5단계 필터링 파이프라인(소외 섹터 → 섹터 리더 → 펀더멘털 개선 → 밸류에이션 → 재무 건전성) 기반 종목 스크리너.
> 이 문서는 설계 논의 과정에서 나온 결정사항과 그 이유를 기록한다. "왜 이렇게 했는지" 잊었을 때 참고.

## 폴더 구조

```
stock_screener/
├── config/
│   ├── endpoints.json     # API 관리 (rate limit, retry 설정)
│   └── params.yaml         # 모든 임계치·가중치 (하드코딩 금지)
├── data/
│   └── loader.py            # 시세/재무/섹터 데이터 수집·캐싱
├── stages/
│   ├── stage1_neglected_sector.py
│   ├── stage2_sector_leaders.py
│   ├── stage3_fundamental_improve.py
│   ├── stage4_valuation.py
│   └── stage5_financial_health.py
├── core/
│   ├── pipeline.py          # 단계 실행 오케스트레이터
│   └── schema.py            # 데이터클래스 (입출력 표준 정의)
├── backtest/
│   └── forward_return.py    # 각 단계별 신호 검증
└── main.py                  # 최종 실행 진입점
```

### 역할 분담

- **`main.py`**: 실행 진입점. CLI 인자 파싱, 실행 날짜 지정, 결과 출력/저장만 담당. 필터링 로직은 모른다.
- **`core/pipeline.py`**: 오케스트레이터. loader의 raw 데이터를 `StockProfile`로 감싸고, stage1~5를 순차 실행하며 탈락 종목을 걸러내는 깔때기(N → M → K → ...) 로직을 담당. `backtest/forward_return.py`에서도 재사용 가능해야 하므로 stage 로직과 분리 유지.
- **`stages/*.py`**: `StockProfile` 하나를 받아 판정 결과만 반환하는 순수 함수 지향. 독립 테스트 용이성 확보.
- **`core/schema.py`**: 파이프라인 전체를 관통하는 State Machine 데이터 규격. "언제 stage를 부를지"는 모르고 "상태를 어떻게 기록할지"만 안다.

### 결정 대기 중

- `mark_failed()`된 종목을 pipeline이 완전히 버릴지, 탈락 사유를 남긴 채 최종 리포트에 포함시킬지 — 사후 분석(왜 stage2에서 떨어졌는지)이 필요하면 후자로.

---

## core/schema.py

### 잘된 점
- `StageEvent` 이력 스트림으로 종목별 상태 전이를 로깅 (ROS 패턴)
- `inject_sector_info`에서 `sector_info.base_date > self.info.base_date` 체크로 look-ahead bias 방어

### 개선 예정 (치명적 아님, 스케일 커지면 반영)
1. **Stage enum ↔ 파일명 불일치**: `QUALITY`(stage2_sector_leaders), `TURNAROUND`(stage3_fundamental_improve) — enum명을 파일명과 맞추거나 매핑 주석 필요.
2. **잘못된 상태 전이 가드 부재**: `mark_failed()` 이후 실수로 `advance_stage()`가 호출되면 이미 FAILED인 종목이 PASSED로 재기록됨. `advance_stage`에 `fail_reason is not None` 체크 추가 예정.
3. **`Stage`를 `IntEnum`으로**: 현재 일반 `Enum`이라 단계 간 비교(`>`, `<`)가 안 됨.
4. **`na_reasons` 필드 4개 클래스 중복**: `MetricsBase` 공통 클래스로 추출 가능 (dataclass 상속 시 `kw_only=True` 필요).
5. **`current_stage` 편의 프로퍼티** 추가 예정.

---

## data/loader.py — Point-in-Time 데이터 수집

### 핵심 설계 결정: FDR → pykrx 전환
초기 버전은 `fdr.StockListing('KOSPI')`로 유니버스를 가져왔는데, 이는 **오늘 기준 스냅샷만 제공**하고 과거 시점 데이터를 못 만든다. `schema.py`의 look-ahead bias 방어 로직이 무의미해지는 문제.

→ `pykrx.stock.get_market_cap(date_str, ...)`로 전환하여 **실제 point-in-time 유니버스** 구축.

### 해결된 데이터 quality 이슈

| # | 문제 | 해결 |
|---|---|---|
| 1 | DART `finstate_all`에 연결(CFS)·별도(OFS) 재무제표가 섞여 있어 계정 매칭이 랜덤하게 오염 | `fs_div='CFS'` 우선 요청, 없으면 `fs_div='OFS'` 폴백. 캐시 파일명에도 `fs_div` 반영 |
| 2 | `sj_div`(BS/IS/CF) 필터 없이 계정명만으로 매칭 → 안전망 부재 | `ACCOUNT_MAPPING`에 `sj` 키 추가, `sj_div` 필터링 후 매칭 |
| 3 | pykrx 전환 후 섹터 정보 소실 | `fdr.StockListing('KRX-DESC')`의 `Industry` 컬럼 병합 (KOSPI보다 커버리지 넓음) |
| 4 | 우선주가 시가총액/섹터 랭킹에 섞임 | 티커 끝자리 `'0'` 필터 (완벽하지 않은 휴리스틱이나 충분) |
| 5 | 캐시 인코딩 불일치 (쓰기 `utf-8-sig`, 읽기 기본값) → 티커 앞자리 0 잘림 위험 | 읽기/쓰기 모두 `utf-8-sig`로 통일 |
| 6 | `config/endpoints.json` 상대경로 → cwd에 따라 `FileNotFoundError` | `Path(__file__).resolve().parent.parent` 기반 절대경로 |
| 7 | DART API 재시도/rate limit 로직 부재 | `dart_retries` + `time.sleep(1)` 재시도 로직 추가 |
| 8 | 캐시 무효화 전략 부재 (정정공시 시 stale) | `_is_cache_valid`로 30일(`cache_days`) 만료 |

### 알려진 한계 (당장 급하지 않음)
- **생존편향(Survivorship Bias)**: `fdr.StockListing('KRX-DESC')`는 현재 상장 종목만 포함. 과거 시점에 상장폐지된 종목은 섹터가 `'기타'`로 뭉뚱그려짐. `missing_sector_ratio > 5%` 시 경고 로깅 추가함 — 백테스트 해석 시 참고할 것.
- **과거 시점 캐시 만료 정책**: 확정된 과거 데이터(`base_date` < 오늘-7일)는 절대 안 바뀌므로 30일 만료 룰이 비효율적. 필요 시 개선.
- **DART 일일 호출량 관리 부재**: 종목당 CFS/OFS + 분기 n개 조합으로 호출 수가 누적되면 전종목 스크리닝 시 일일 한도 소진 위험. `daily_limit` 카운터 추가 검토 필요.

### 다음 확장 예정 (stage 구현을 위해 필요)
1. **OHLCV 시계열 메서드**: stage1 소외도 계산(6개월 섹터별 수익률/거래대금 z-score)에 필요. `fdr.DataReader` 또는 pykrx 시계열 활용.
2. **분기 단독값 차분(isolation) wrapper**: `get_isolated_quarterly_financials()`. 1·3분기 누적치 vs 단독치 문제 — **BS 항목(잔액)은 차분하면 안 되고 IS/CF 항목(흐름)만 차분 대상**. 구현 중 발견된 버그: `thstrm_nm` 텍스트 매칭으로 "누적/단독"을 판별하는 방식은 보고서마다 표기가 달라 신뢰 불가 → `thstrm_add_amount` 컬럼 존재 여부로 판별하는 방식 검토 중.
3. **추가 계정 매핑**: 자산총계, 부채총계, 자본총계, 이자비용 (부채비율/ROE/이자보상배율용). BPS는 계정과목이 아니라 `자본총계 ÷ 발행주식수`로 별도 계산 필요.

---

## 지표 계산 기준 — TTM vs 연간 확정치

Stage2(ROE, ROIC, 영업이익률) 계산 기준 논의 결과:

**결정: TTM(최근 4분기 합산) 채택.** 스크리너의 목적이 "실적 개선 조기 포착"(Stage3가 turnaround 감지)이라 연간 확정치만 쓰면 최대 12~15개월 지연 발생.

### 계산 원칙
- **분자(흐름값: 순이익, 영업이익, 매출액)**: 4개 분기 합산 (TTM)
- **분모(잔액값: 자기자본, 투하자본)**: 최근 분기말 스냅샷 그대로 사용, 절대 합산하지 않음

```
ROE  = TTM 당기순이익 (4분기 합산) / 최근 분기말 자기자본 (스냅샷)
ROIC = TTM NOPAT (4분기 합산) / 최근 분기말 투하자본 (스냅샷)
영업이익률 = TTM 영업이익 / TTM 매출액   (둘 다 흐름값이라 합산 OK)
```

### 필수 검증: 공시 시차(disclosure lag)
분기보고서는 분기말 이후 45일, 사업보고서는 회계연도말 이후 90일이 법정 제출기한. `report_code`만으로 "이 분기 데이터"라 가정하면 안 되고, **`rcept_dt`(접수일자) < `base_date`** 검증이 반드시 필요 (schema.py의 `inject_sector_info` base_date 검증과 동일한 원칙을 재무데이터에도 적용).

### config/params.yaml 반영 예정 항목
```yaml
profitability_basis: "ttm"          # "ttm" | "annual"
ttm_denominator: "latest_snapshot"  # "latest_snapshot" | "avg_4q"
disclosure_lag_check: true          # rcept_dt < base_date 강제 검증
```

---

## 다음 작업 순서 (미정 부분)

- [ ] `pipeline.py` 오케스트레이터 구현 (탈락 종목 보존 정책 먼저 결정)
- [ ] `data/loader.py`: OHLCV 시계열 + 분기 차분 wrapper + 추가 계정 매핑
- [ ] `config/params.yaml` 설계 (TTM 기준 등 반영)
- [ ] `stage1_neglected_sector.py` 구현
