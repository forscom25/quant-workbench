# stock_screener 아키텍처 노트

> 5단계 필터링 파이프라인(소외 섹터 → 섹터 리더 → 펀더멘털 개선 → 밸류에이션 → 재무 건전성) 기반 종목 스크리너.
> 이 문서는 **항상 현재 확정된 설계 상태**를 반영한다. 논의 과정·미결 항목·진행 메모는 [`docs/decisions_log.md`](./decisions_log.md) 참고.
> 평가 기준(임계치, 개선 근거)은 [`docs/screening_criteria.md`](./screening_criteria.md) 참고.

## 폴더 구조

```
stock_screener/
├── config/
│   ├── endpoints.json      # API 관리 (rate limit, retry 설정)
│   └── params.yaml          # 모든 임계치·가중치 (하드코딩 금지)
├── data/
│   └── loader.py             # 시세/재무/섹터 원자료 수집·캐싱 (계산 없음)
├── stages/
│   ├── stage1_neglected_sector.py
│   ├── stage2_sector_leaders.py
│   ├── stage3_fundamental_improve.py
│   ├── stage4_valuation.py
│   └── stage5_financial_health.py
├── core/
│   ├── pipeline.py           # 단계 실행 오케스트레이터 (계산 없음)
│   ├── schema.py             # 데이터클래스 (입출력 표준 정의)
│   └── metrics_utils.py      # 재사용 가능한 순수 통계 함수 (std, 추세 판정 등)
├── backtest/
│   └── forward_return.py     # 각 단계별 신호 검증
└── main.py                   # 최종 실행 진입점
```

### 역할 분담

- **`main.py`**: 실행 진입점. CLI 인자 파싱, 실행 날짜 지정, 결과 출력/저장만 담당. 필터링 로직은 모른다.
- **`core/pipeline.py`**: 오케스트레이터. loader의 raw 데이터를 `StockProfile`로 감싸고, stage1~5를 순차 실행하며 탈락 종목을 걸러내는 깔때기(N → M → K → ...) 로직을 담당. `backtest/forward_return.py`에서도 재사용 가능해야 하므로 stage 로직과 분리 유지. **계산 로직 0줄 원칙** — 조건문(`if roe > ...` 등)이 들어간다면 stage로 옮겨야 한다는 신호. **(구현 완료)** 실제 구현에서도 조건문·계산 로직 없이 각 stage 인스턴스를 순차 호출해 데이터프레임만 전달하는 순수 흐름 제어기로 확인됨. 탈락 종목은 버리지 않고 단계별 통과 데이터프레임을 `history` 딕셔너리에 담아 최종 결과와 함께 반환.
- **`stages/*.py`**: `StockProfile` 하나를 받아 지표를 계산하고 판정 결과만 반환하는 순수 함수 지향. 독립 테스트 용이성 확보.
- **`core/schema.py`**: 파이프라인 전체를 관통하는 State Machine 데이터 규격. "언제 stage를 부를지"는 모르고 "상태를 어떻게 기록할지"만 안다.
- **`core/metrics_utils.py`**: stage들이 공통으로 쓰는 순수 통계 함수 모음 (표준편차, 추세 판정 등). 특정 stage나 지표에 종속되지 않음.

### loader / pipeline / stage 책임 경계

**판단 기준(한 줄 테스트)**: "이 로직이 `params.yaml`의 값이 바뀌면 결과가 달라지는가?"
- Yes → **stage**의 몫 (전략/기준 판단)
- No, 데이터 자체의 정합성 문제 → **loader**의 몫 (데이터 정제)

| 구분 | loader.py | pipeline.py | stages/*.py |
|---|---|---|---|
| 원본 수집/캐싱 | ✅ | ❌ | ❌ |
| 계정명 표준화 (CFS/OFS, sj_div) | ✅ | ❌ | ❌ |
| point-in-time 정합성 (base_date 검증) | ✅ | ❌ | ❌ |
| flow/stock 구분 (TTM 합산 vs 스냅샷) | ✅ | ❌ | ❌ |
| 시계열 원자료 취득 (분기별 리스트 등) | ✅ | ❌ | ❌ |
| 비율 계산 (ROE, 표준편차 등) | ❌ | ❌ | ✅ (metrics_utils 호출) |
| pass/fail 판정 (params.yaml 임계치 비교) | ❌ | ❌ | ✅ |
| StockProfile 객체 조립 | ❌ | ✅ | ❌ |
| stage 순차 호출·탈락 종목 관리 | ❌ | ✅ | ❌ |

**적용 예시 — TTM 합산 여부는 stage가 결정, 계산 규칙은 loader가 보장**:
```python
# loader.py — 도구만 제공, 어떤 걸 쓸지는 모름
def get_ttm_financials(self, ticker, base_date) -> dict: ...
def get_annual_financials(self, ticker, base_date) -> dict: ...

# stage2_sector_leaders.py — params.yaml 보고 결정 + 비율 계산
basis = params['profitability_basis']
raw = loader.get_ttm_financials(...) if basis == 'ttm' else loader.get_annual_financials(...)
roe = raw['net_income'] / raw['equity']
```

### 탈락 종목 처리 방식 (구현 완료)

`mark_failed()`된 종목은 pipeline에서 완전히 버리지 않는다. `core/pipeline.py`가 단계별 통과 데이터프레임을 `history` 딕셔너리에 담아 최종 결과와 함께 반환하는 방식으로 확정 — 사후 분석("이 종목이 왜 stage2에서 떨어졌는지")과 디버깅이 쉬워짐. 별도의 복잡한 상태 객체 대신 단순 딕셔너리 축적으로 유지.

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
| 9 | DART 일일 호출량 관리 부재 (CFS/OFS 이중 호출 + 분기 n개 조합 시 한도 소진 위험) | `dart_daily_limit` 카운터 구현. 한도 도달 시 조용히 새지 않고 `RuntimeError`로 스크리닝 즉시 중단해 원인 파악 쉽게 처리 |

### 알려진 한계 (당장 급하지 않음)
- **생존편향(Survivorship Bias)**: `fdr.StockListing('KRX-DESC')`는 현재 상장 종목만 포함. 과거 시점에 상장폐지된 종목은 섹터가 `'기타'`로 뭉뚱그려짐. `missing_sector_ratio > 5%` 시 경고 로깅 추가함 — 백테스트 해석 시 참고할 것.
- **과거 시점 캐시 만료 정책**: 확정된 과거 데이터(`base_date` < 오늘-7일)는 절대 안 바뀌므로 30일 만료 룰이 비효율적. 필요 시 개선.

### 밸류에이션 원자료는 pykrx 기시산출값 사용
PBR/BPS/PER은 DART 계정을 조합해 직접 계산하지 않고 **`pykrx.stock.get_market_fundamental(date, market="KOSPI")`**로 바로 받는다. KRX가 point-in-time 기준으로 이미 계산해 제공하며, "발행주식수 별도 조회" 문제를 우회할 수 있다.
- ROE/ROIC/영업이익률은 반대로 **DART 원본 계정으로 직접 계산 유지** — 제공자마다 정의(평균자기자본 vs 스냅샷, 투하자본 정의 등)가 달라 이 스크리너의 계산 규칙(TTM 분자·스냅샷 분모)을 스스로 통제해야 하기 때문.

### 확장된 원자료 메서드 (구현 완료)
1. **OHLCV 시계열**: stage1 소외도 계산(6개월 섹터별 수익률/거래대금 z-score)용. `fdr.DataReader`/pykrx 시계열 기반 구현.
2. **분기 단독값 차분(isolation) 로직**: 텍스트(`thstrm_nm`) 매칭 방식은 보고서마다 표기가 달라 신뢰 불가하고 실제 이중 차감 버그로 이어져 폐기. **`thstrm_add_amount` 컬럼 존재 여부**로 누적/단독을 판별하는 try-except 구조로 확정 및 구현 완료.
   - ⚠️ **검증 권장**: `thstrm_add_amount`/`thstrm_amount` 중 어느 쪽이 누적이고 단독인지는 계정·보고서 종류별로 raw 응답을 직접 대조해 재확인해두는 걸 권장 (이전 버그도 확인 없이 가정했다가 발생했던 것이므로).
   - **BS(잔액) 항목은 차분 대상 아님** — `sj_div`로 분기해 BS는 스냅샷 그대로, IS/CF만 차분 적용.
3. **추가 계정 매핑**: 자산총계, 부채총계, 자본총계, 이자비용 (부채비율/ROE/이자보상배율용) 반영 완료.
4. **`get_quarterly_op_margin_series(ticker, base_date, n_quarters=8)`**: Stage2 `op_margin_std`(변동성) 계산용. TTM이 아닌 분기 **단독**값으로 반환, 결측 분기는 `float('nan')`으로 채움.
5. **`get_quarterly_financials_series()`**: 분기별 재무 시계열을 묶어서 반환하는 범용 wrapper (Stage3/4의 YoY 비교용).
6. **`get_market_fundamental_cross_section(base_date)`**: pykrx `get_market_fundamental` 기반 point-in-time PBR/BPS/PER 스냅샷.

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
op_margin_lookback_q: 8             # 변동성 계산용 시계열 길이
op_margin_min_quarters: 4           # 최소 유효 분기 수 (미만이면 NOT_COMPUTABLE)
```

---

## core/metrics_utils.py — 재사용 통계 함수

여러 stage에서 "시계열을 놓고 변동성/추세를 판단"하는 패턴이 반복될 것으로 예상 (Stage2 `op_margin_std`, Stage3 `is_sga_decreasing_consecutively` 등). 각 stage에 흩어져 중복·미묘하게 다른 구현(ddof 차이, 결측 처리 차이)이 생기는 걸 막기 위해 공용 순수 함수로 관리한다.

```python
def compute_std(series: list[float], min_valid_points: int = 4) -> tuple[float, MetricStatus]:
    """결측(nan) 제외 후 표본표준편차 계산. 유효 표본 부족 시 NOT_COMPUTABLE."""
    valid = [x for x in series if not pd.isna(x)]
    if len(valid) < min_valid_points:
        return float('nan'), MetricStatus.NOT_COMPUTABLE
    return float(np.std(valid, ddof=1)), MetricStatus.COMPUTED
```

`min_valid_points` 같은 기준값은 함수 내부에 하드코딩하지 않고 호출부(stage)가 `params.yaml`에서 읽어 전달한다.

**구현 완료.** `compute_std()`가 `NOT_COMPUTABLE`을 부여하면, 이를 곧바로 탈락시키지 않고 각 stage가 예외(exempt) 조건으로 구제하여 억울한 탈락을 방지하는 원칙을 채택 — 이 처리 정책의 구체적 태그명(`OP_MARGIN_STD_NOT_COMPUTABLE` 등)과 stage별 적용 방식은 [`screening_criteria.md`](./screening_criteria.md#공통-설계-원칙)에 정리.

---

## 다음 작업 순서

- [x] `pipeline.py` 오케스트레이터 구현 완료 (`history` 이력 관리 포함, 계산 로직 0줄 원칙 확인됨)
- [x] `data/loader.py`: OHLCV 시계열, 분기 차분 wrapper, 추가 계정 매핑, `get_quarterly_op_margin_series`/`get_quarterly_financials_series`/`get_market_fundamental_cross_section` 구현 완료
- [x] `core/metrics_utils.py` 신설 및 `compute_std` 순수 함수 분리 완료
- [x] `config/params.yaml` 설계 완료 (Stage 1~5 전체 파라미터 매핑)
- [x] Stage 1~5 스크리너 클래스 독립 구현 완료

### 남은 미결 항목
- [ ] `op_margin_min_quarters` 기본값(4 vs 6) — 구현은 완료됐으나 값 자체는 백테스트로 튜닝 필요
- [ ] `thstrm_add_amount`/`thstrm_amount` 중 어느 쪽이 누적·단독인지 raw 응답으로 재검증 (계정·보고서 종류별)
- [ ] Stage4의 PBR `NaN`/자본잠식 임시 구제 통과가 Stage5에서 실제로 재검증되는지 pipeline 흐름 확인
- [ ] `backtest/forward_return.py` 구현 (아직 미착수)
- [ ] `main.py` 실행 진입점 구현 (아직 미착수)
