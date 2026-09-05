# 진행 상황 & 논의 로그 (Decisions Log)

시간순으로 쌓이는 로그. 확정된 설계는 아래 규칙에 따라 [`architecture.md`](./architecture.md) 또는 [`screening_criteria.md`](./screening_criteria.md)로 "승격"되고, 여기엔 반영 완료 표시만 남는다.

**운영 규칙**
- 모든 논의는 일단 여기에 먼저 쌓인다.
- 논의가 끝나고 코드/문서에 반영되면 결론만 architecture.md 또는 screening_criteria.md로 옮기고, 여기엔 `→ architecture.md 반영 완료` 식으로 표시.
- 상태 태그: `[결정]` `[진행 중]` `[보류]`

---

## 2026-07-31

### [결정] 문서 관리 방식 이원화
- `architecture.md` / `screening_criteria.md`: 항상 "현재 확정 상태"만 반영하는 문서
- `decisions_log.md`(본 파일): 논의 과정, 진행 메모, 미결 항목을 시간순으로 기록
- → 자기 자신 반영 완료 (본 파일 신설로 확정)

### [결정] loader / pipeline / stage 책임 경계
- 판단 기준: "params.yaml 값에 따라 결과가 바뀌는가?" → Yes면 stage, No(데이터 정합성)면 loader
- pipeline.py는 계산 로직 0줄 원칙 (조건문 있으면 stage로 이동 신호)
- → architecture.md 반영 완료

### [결정] op_margin_std 계산 위치 및 방식
- loader: `get_quarterly_op_margin_series()` — 분기 **단독**값 시계열 반환 (TTM 아님, 결측은 nan 유지)
- core/metrics_utils.py 신설: `compute_std()` 등 재사용 가능한 통계 함수
- stage2: 유틸 호출 + 판정만 수행
- → architecture.md, screening_criteria.md 반영 완료

### [결정] 밸류에이션(PBR/BPS) 데이터 소스
- DART 계정 조합 대신 `pykrx.stock.get_market_fundamental()`의 point-in-time 기시산출값 사용
- 반대로 ROE/ROIC/영업이익률은 정의 통제(TTM 분자·스냅샷 분모 규칙 유지)를 위해 DART 원본 계정으로 직접 계산 유지
- → architecture.md, screening_criteria.md 반영 완료

### [결정] 탈락 종목 보존 정책
- **확정**: 종목을 완전히 버리지 않고, `pipeline.py`에서 단계별 통과 데이터프레임을 `history` 딕셔너리에 담아 최종 결과와 함께 반환하는 방식 채택 (강제 폐기나 복잡한 상태 객체 대신 단순 딕셔너리 축적)
- 사유: 사후 분석("이 종목이 왜 stage2에서 떨어졌는지") 및 디버깅 용이성 확보
- → pipeline.py 구현 완료. architecture.md 반영 완료

### [결정] DART 일일 호출량 관리
- **확정**: `loader.py`에 `dart_daily_limit` 카운터 구현. 한도 도달 시 `RuntimeError`로 스크리닝 즉시 중단 (조용히 `NOT_COMPUTABLE`로 새는 대신 명시적으로 실패시켜 원인 파악 쉽게)
- → loader.py 구현 완료. architecture.md 반영 완료

### [결정] 통계 계산 표본 수 부족 처리
- `op_margin_std` 등 계산 시 신규 상장주처럼 데이터가 부족한 경우, `metrics_utils.py`가 `NOT_COMPUTABLE` 상태를 부여
- Stage 필터링 단계에서는 이를 예외(exempt) 조건으로 구제하여 억울한 탈락 방지 — 이 원칙은 다른 stage의 유사 케이스(`ROIC_NOT_COMPUTABLE` 등)에도 동일하게 적용됨
- → screening_criteria.md 공통 설계 원칙(3-1) 및 각 stage 절 반영 완료

### [결정] 분기 단독값 판별 및 차분(Isolation) 방식
- DART의 IS/CF(손익/현금흐름) 누적치 특성을 고려, 매 분기 누적치를 조달한 뒤 직전 분기 누적치를 차감하여 단독 분기값을 산출하는 방식을 원칙으로 확정. 
- (단, BS(재무상태표) 항목은 스냅샷이므로 차분 제외)
- ⚠️ **검증 완료**: 삼성전자 샘플 대조를 통해 누적/단독값 파싱 로직 및 차분 계산 정합성 완벽히 검증됨.
- → loader.py 구현 완료. architecture.md 반영 완료

## 2026-08-01

### [결정] 파이프라인 상태 누적(Accumulation) 로직 확정
- **이슈**: Stage 4에서 밸류트랩 검증 시, Stage 2에서 계산된 `roe`가 유실되어 `KeyError`가 발생하는 현상 확인.
- **해결**: `pipeline.py`에 `_accumulate_results` 메서드를 신설. 각 stage에서 산출된 새로운 지표들을 `ticker` 기준으로 병합(Inner Merge)하여 다음 단계로 넘겨주도록 확정. 
- → `architecture.md` 및 `screening_criteria.md` 반영 완료

### [결정] 공시 시차(Disclosure Lag) 동적 탐색 도입
- **이슈**: 달력상의 월을 기준으로 최근 분기를 단순 역산할 경우, 아직 실적이 공시되지 않은 분기(`t=0`)를 참조하여 빈 데이터가 반환되는 문제 확인.
- **해결**: `loader.py`의 `get_quarterly_financials_series` 메서드가 DART API를 호출하여 실제 매출액 데이터가 존재하는 가장 최신 분기를 동적으로 탐색하고, 이를 `t=0` 기점으로 확정하도록 로직 개선.
- → `architecture.md` 반영 완료

## 2026-08-02

### [결정] 백테스트 엔진(forward_return.py) 검증 완료
- **진행**: T+1 진입 및 슬리피지/수수료가 반영된 `BacktestEngine` 설계 및 단일 시점(2023-06, 2023-09) 구동 테스트 완료.
- **결과**: `KS11`(코스피) 벤치마크 수익률 정상 산출 및 포트폴리오 수익률 기록 로직 검증 완료. 자산(Capital) 추적 로직은 순수 알파 추적을 위해 엔진에서 제거함.
- → backtest/forward_return.py 구현 완료. architecture.md 반영 완료

## 2026-08-03

### [결정] Stage 3 & 4 아키텍처 전면 개편 (Composite Score 도입)
- **이슈**: 다중 시점 백테스트(23.03, 23.06) 수행 결과, Stage 3(92% 탈락)와 Stage 4(100% 탈락)에서 극심한 병목 현상이 반복됨을 확인. 
- **원인**: 기존의 이진(Boolean) 방식의 절대 컷오프(Hard Cut)가 시장 체제 변화에 유연하게 대응하지 못해 유망 성장주들을 과도하게 누락시킴. (분석 결과 `loader.py`의 차분 버그가 아님이 증명됨)
- **해결책**:
  1. Stage 3와 4의 필터링 로직을 절대 컷오프에서 **Z-score 기반의 가중 합산 점수(Composite Score)** 산출 방식으로 전면 교체.
  2. 임계치 미달(매출 역성장, 밸류 트랩 등)로 인한 무조건적인 탈락을 폐지하고, `is_cost_cutting_warning`, `is_pbr_value_trap` 형태의 경고 태그(Tag)만 부여.
  3. 공통 산출 로직(`calc_zscore`, `compute_composite_score`, `apply_percentile_filter`)을 `core/metrics_utils.py`로 추상화하여 코드 재사용성 극대화.
- → stage3, stage4, metrics_utils.py 개편 완료. architecture.md 및 params.yaml 반영 완료

### [진행 중] 스크리닝 파라미터(Percentile) 완화 튜닝
- 단일 종목(삼천리) 생존으로 엔진 검증은 마쳤으나, 분산 투자를 위한 포트폴리오(10~20개 종목) 구성을 위해 Stage 3, 4의 `composite_pass_percentile`을 30%에서 50% 수준으로 완화하여 백테스트 재구동 및 튜닝 진행 예정.

## 2026-08-04

### [결정] Stage 5 아키텍처 개편 (Composite Score 도입 및 절대 컷오프 폐지)
- **이슈**: 턴어라운드 및 폭발적 성장 잠재력을 가진 기업들이 시설 투자로 인한 부채비율 상승과 1.5배라는 보수적인 이자보상배율 컷오프에 걸려 전멸하는 현상 발견
- **해결책**:
  1. Stage 5를 이진 컷오프에서 Z-score 기반 가중 합산 점수(Composite Score)로 개편. 조기 은퇴 포트폴리오의 핵심인 자본 증식 속도를 고려하여 빚 자체(부채비율 30%)보다 빚을 갚을 현금 창출력(ICR 70%)에 압도적 가중치 부여.
  2. 결격 사유는 `[ICR미달]`, `[과다부채]`, `[이익질주의]` 등의 경고 태그(Warning Tag)로 전환.
- → stage5.py, metrics_utils.py 개편 완료. architecture.md 및 params.yaml 반영 완료

### [결정] Stage 5 통계 산출 그룹(Pool) 변경 (생존자 편향 버그 수정)
- **이슈**: Stage 5 진입 시점에 남은 종목이 극소수라 섹터별로 묶을 경우 표본 수 부족(N=1)으로 표준편차가 `NaN`이 되어 점수가 붕괴되는 현상 발생
- **해결책**: 최종 단계의 특수성을 감안하여 Z-score 산출 시 `.groupby('sector')`를 제거하고, 전체 생존자를 하나의 풀(Pool)로 묶어 상대 평가를 진행하도록 수정
- → stage5.py 반영 완료

## 2026-08-07

### [결정] 백테스트 및 분석 계층 분리 (CQS 아키텍처 도입)
- **내용**: 백테스트 실행과 분석 과정을 명령-조회 분리(CQS) 원칙에 따라 분리함.
  1. `run_backtest.py`: 기간 설정, 캐시 웜업, 파이프라인 트리거를 담당하는 얇은 진입점.
  2. `backtest/forward_return.py`: T+1 종가 진입, 수수료/슬리피지 차감, 시점 순회를 담당하는 순수 오케스트레이터(엔진). 기존 OOP(클래스) 구조 유지.
  3. `analysis/` (`stats.py`, `visualize.py`): 백테스트 결과물(CSV)을 읽기만(Read-only) 하여 성과 지표(Sharpe, MDD 등) 계산 및 차트를 렌더링하는 조회 구역.
- → `architecture.md`, `analysis/` 폴더 구조 및 `.gitignore` 반영 완료.

### [결정] 영업일 추출 로직 확정 (pykrx 버그 우회)
- **이슈**: `pykrx` 라이브러리의 캘린더 함수(`get_business_days` 등) Deprecation 및 내부 타입 에러 버그, 그리고 휴일 보정 과정의 과도한 API 호출로 인한 서버 차단 문제 발생.
- **해결책**: 분기말 기준일(`base_dates`) 생성 시, KOSPI 지수의 실제 시세 데이터를 조회하여 데이터가 존재하는 가장 가까운 과거 거래일을 직접 추출하는 우회 로직으로 확정.
- → `run_backtest.py` 반영 완료. **(2026-09-01 갱신: 이후 리팩터로 `loader.py`의 `get_quarterly_rebalance_dates`/`_get_nearest_past_bday`로 이관, 데이터 소스도 pykrx `get_index_ohlcv`에서 FinanceDataReader `KS11`로 교체됨. 최신 위치는 architecture.md §2.1 참고.)**

### [결정] 캐시 디렉토리 절대 경로 고정
- **이슈**: 스크립트 실행 위치(터미널 루트)에 따라 캐시 폴더가 의도치 않은 곳에 생성되는 현상.
- **해결책**: `loader.py` 내부에서 `Path(__file__).resolve().parent`를 사용하여 캐시 폴더 생성 위치를 `data/.cache`로 강제 고정함.
- → `loader.py` 반영 완료.

### [진행 중] 성장주 타겟팅 파라미터 리모델링 기획
- **논의**: 조기 은퇴 목표를 위한 자본 증식 가속화를 위해, 무거운 배당주를 배제하고 성장주에 확실한 프리미엄을 부여하는 방향으로 가중치 재편을 기획함.
  - **Stage 3**: 단순 비용 절감보다 폭발적 외형 성장에 집중 (매출 0.6, GPM 0.3, 판관비 0.1)
  - **Stage 4**: 가치 함정 방지 및 장부가액의 복리 증식 극대화 (BPS 성장 0.8, PBR 0.2)
- **보류 사항**: 파이프라인 통과 비율(상위 30%)은 우선 유지하기로 결정함. API 한도 초기화 후 백테스트를 돌려보고, 최종 생존 종목 수에 따라 허들 상향(예: 상위 20%로 압축) 여부를 추후 재평가할 예정.
- → 가중치 비율 `params.yaml` 반영 완료 (2026-09-01 확인: Stage3 매출0.6/판관비0.1/GPM0.3, Stage4 PBR0.2/BPS0.8로 이미 적용되어 있었음 — `architecture.md` §4 표가 예전 값(0.4/0.4/0.2, 0.5/0.5)으로 남아있던 것을 이번에 갱신). 컷오프 비율(상위 30%)은 여전히 미확정 상태로 유지.

## 2026-09-01

### [결정] 문서-구현 정합화 작업 (아키텍처 방향 정리)
- **이슈**: "한 문제가 해결되면 새 워크스페이스를 열어 독립적으로 개발"하는 방식이 반복되며 `core/schema.py`가 여러 세션에 걸쳐 서로 다른 설계(클래스 기반 상태 머신 → DataFrame 스키마 계약)로 다시 쓰였고, 그 결과 Stage3/4만 새 패턴("탈락 종목을 `fail_reason` 컬럼으로 보존")으로 옮겨가고 Stage1/2/5는 옛 방식(탈락 시 row 자체를 드롭)에 머물러 파이프라인 내부에서 단계별로 설계가 갈라져 있었음. `docs/` 세 문서도 실제 구현과 상당 부분 어긋나 있었음(예: 한 번도 쓰인 적 없는 `StockProfile`/`inject_sector_info` 상태 머신을 "확정 설계"로 서술).
- **해결책**:
  1. `fail_reason` 컬럼 기반 탈락 종목 보존 패턴을 Stage1/2/5까지 전체 확장. Stage1은 섹터 단위 판정을 티커 단위로 전개하며 `SECTOR_NOT_QUALIFIED` 사유를 상속시킴. Stage2는 boolean AND 컷오프 결과를 `QUALITY_CUTOFF_NOT_MET`으로 태깅. Stage5는 기존 `apply_percentile_filter`(row 드롭)를 Stage3/4와 동일한 컷오프 태깅 방식으로 교체.
  2. `pipeline._accumulate_results`가 이전 단계의 (항상 None인) `fail_reason`을 새 stage의 실제 판정값으로 덮어쓰지 않고 그대로 두던 버그를 발견 및 수정 — 고치지 않았다면 Stage1 이후 어떤 단계의 탈락 판정도 실제로 반영되지 않는 상태였음.
  3. `core/metrics_utils.py`가 `core/schema.py`와 별도로 자체 `MetricStatus`를 정의하고 있던 것을 schema.py 것으로 통일. Stage5의 `MetricStatus.CAUTION`을 문자열 `join()`에 그대로 섞어 쓰던 잠재 버그(Enum화 시 TypeError 유발)를 리터럴 태그 문자열로 교체.
  4. `Stage` enum에서 실제로 쓰이지 않던 `UNIVERSE`/`PRE_FILTER` 멤버 제거, 누락되어 있던 `STAGE5` 추가.
  5. `ValuationMetrics`에 `per` 필드를 실제 값으로 채움(기존에는 pykrx 응답에 이미 들어있는 값을 로더가 버리고 있었음). `psr`은 별도 DART 조회가 필요한 신규 기능으로 판단해 스키마에서 제거하고 TODO로 이관.
  6. `analysis/visualize.py`가 존재하지 않는 `mdd`/`cumulative_return` 컬럼을 직접 읽으려던 것을, `analysis/stats.py`의 `calc_mdd`/`calc_cumulative_returns`를 호출하도록 연결 — CQS 원칙(연산은 stats.py, 렌더링은 visualize.py)을 실제로 지키게 됨.
  7. `main.py`(실전 스크리닝 진입점)는 현재 빈 파일 상태로 당장 착수하지 않기로 결정 — 아직 백테스트/파라미터 튜닝 단계이므로 우선순위 낮음.
- → `core/schema.py`, `core/metrics_utils.py`, `core/pipeline.py`, `stages/*.py`, `data/loader.py`, `analysis/visualize.py` 반영 완료. `architecture.md`, `screening_criteria.md` 반영 완료.

## 2026-09-06

### [결정] `data/cache` 실데이터 기반 알고리즘 로직 재점검
- **배경**: 본격적인 코드 수정에 앞서 현재 알고리즘의 예상 문제점을 점검. `data/cache`에 쌓인 DART 원자료를 실제로 까보면서 계정 매핑의 타당성을 검증하는 방식으로 진행하기로 함.
- **발견**: `interest_expense` 계정 매핑이 "금융비용/금융원가"(FX·파생 손익 등이 섞인 광의 개념)를 정확한 계정보다 우선 매치하는 구조적 결함 발견 — 캐시 150개 표본 검증 결과 87개(70%)가 20%p 이상 괴리, 일부는 수백 배 차이. Stage2 ROIC의 분모(`total_assets - total_liabilities`)가 회계항등식상 자기자본과 동일해 사실상 ROIC가 아닌 ROE의 변형이었음도 확인.
- **해결책**: `interest_expense`를 정확ID→P&L 세부항목→CF 이자지급 라인→광의 금융비용 4단계 우선순위로 재구성(300개 표본 재검증 결과 83%가 CF 라인, 9%만 광의 금융비용에 의존). ROIC 분모를 `total_borrowings + total_equity`(진짜 투하자본)로 수정, DART 차입금 계정이 회사마다 제각각(8종 이상 확인)이라 매칭 실패 시 무차입(0)으로 간주. PER은 pykrx 응답에 이미 있는데 버려지고 있던 값이라 즉시 살림, PSR은 별도 DART 매출 조회가 필요해 TODO로 이관.
- **부수 발견**: 위 계정 매핑 리팩터(정확ID 우선 매칭) 덕분에, 미처 모르고 있던 또 다른 잠재 버그(`total_liabilities`/`total_equity`가 "자본과부채총계"라는 대차대조표 전체합계 문자열에 부분매치될 위험)도 함께 해소됨을 캐시 200개 재검증으로 확인.
- → `data/loader.py`, `stages/stage2_sector_leaders.py` 반영 완료.

### [결정] 남은 문제 우선순위 확정 및 순차 처리 (4→2→3→6→1→5)
- **논의**: 지난 리뷰에서 나열했던 6개 미해결 항목(백테스트 생존편향, 섹터 시총가중, 섹터 라벨 point-in-time, 포지션 수 편차, 공시 시차 fail-open, params.yaml 장식용 설정) 중 어떤 걸 먼저 볼지 사용자가 우선순위 지정 — "look-ahead bias를 줄이는 게 핵심"이라는 기준으로 4번(공시 시차)부터 시작.
- **4번 (공시 시차 fail-open)**: `parse_standardized_financials`의 disclosure-lag 체크가 `rcept_no` 컬럼 누락/파싱 실패 시 조용히 검증을 건너뛰고 데이터를 쓰던 것을 fail-closed로 전환. 캐시 21,768개 전수조사 결과 이 분기가 실제로 발동한 이력은 0건이었으나(현재 데이터엔 영향 없음), 유일한 look-ahead 방지 장치라 안전하게 닫기로 결정.
- **2번 (섹터 시총가중)**: `get_sector_metrics`의 섹터 수익률 집계를 종목 단순평균에서 시가총액가중으로 교체(문서 설계와 실제 코드가 어긋나 있던 부분). `groupby.apply` 대신 `market_cap`을 결측 마스킹한 벡터화 연산으로 구현해 향후 pandas FutureWarning도 회피.
- **3번 (섹터 라벨 point-in-time)**: `fdr.StockListing('KRX-DESC')`가 날짜 인자를 지원하지 않아 항상 "캐시 생성 시점" 기준 업종이 모든 과거 데이터에 붙는 문제. `universe_20190930.csv` vs `universe_20250930.csv`(공통 종목 747개) 비교 결과 섹터 라벨 차이 0건 확인 — 업종 재분류 자체가 드물어 실무 영향이 제한적이라 판단, **수정하지 않고 코드 docstring과 문서에 한계로 명시만 하기로 결정**(과거 시점 업종 분류 데이터 소스가 마땅치 않음).
- **6번 (포지션 수 편차)**: "지수 부진 시 현금 비중을 높이는 것과 같은 논리"로, 통과 종목이 `min_portfolio_size`(기본 5) 미만이면 거래를 스킵하고 현금(0%) 처리하기로 결정. 작업 중 인접 버그(임계치는 충족했으나 전 종목 시세 데이터가 없어 `performance_log`에서 조용히 통째로 누락되던 분기)도 함께 발견해 동일한 현금 처리 방식으로 통일.
- **1번 (생존편향)**: `_get_period_return`이 NaN을 반환한 종목을 조용히 평균에서 제외하던 것을, `_is_delisted`로 해당 시점 KOSPI 유니버스 존재 여부를 확인해 실제 상장폐지면 전손(-100%) 반영, 유니버스엔 남아있는데 시세만 없으면(데이터 품질 이슈) 기존대로 제외하도록 구분.
- **5번 (params.yaml 장식용 설정)**: `global.disclosure_lag_check`는 4번에서 막 강화한 안전장치를 끌 수 있게 하는 게 앞뒤가 안 맞아 아예 삭제. `ttm_denominator`의 `avg_4q`(4분기 평균 분모)는 "외란 대응 장치로 좋다"는 사용자 판단에 따라 실제 구현(증자·자사주매입 등으로 분모 급변 시 완화 효과, ROE 예시로 10%→14.3% 차이 확인). `profitability_basis`의 `"annual"` 경로는 `get_annual_financials()`가 이미 있으나 미배선 상태로 TODO 이관.
- → `data/loader.py`, `backtest/forward_return.py`, `backtest/run_backtest.py`, `backtest/cache_warmup.py`, `config/params.yaml` 반영 완료. `screening_criteria.md` 반영 완료.

### [진행 중] params.yaml 하드코딩 전수점검 (그룹 B 완료, 그룹 A 대기)
- **논의**: 사용자가 params.yaml에 정리할 게 더 있다고 지적(백테스트 시작/종료 날짜 등 예시). 전수 점검 결과 두 그룹으로 분류:
  - **그룹 A (미착수)**: 백테스트 시작/종료 연도가 `run_backtest.py`(argparse 기본값), `cache_warmup.py`의 `get_quarterly_rebalance_dates(2019, 2025)`, `years = list(range(2018, 2026))` 세 곳에 각각 독립적으로 하드코딩되어 있어, 기간을 바꾸려면 3곳을 다 고쳐야 하고 하나라도 빠뜨리면 캐시 예열 범위와 백테스트 범위가 어긋나는 위험 존재. **다음 세션에서 처리 예정.**
  - **그룹 B (완료)**: Stage2 `effective_tax_rate`(0.22), Stage3/4/5의 `calc_zscore` 극단값 클리핑 분위(0.01/0.99), Stage5의 ICR 캡·클리핑·경고 태그 임계치 6종, `BacktestEngine`의 `fee_rate`/`slippage`/`min_portfolio_size`를 전부 params.yaml로 이관. 기본값은 기존 하드코딩 값과 동일하게 유지(회귀 없음)하고, 커스텀 값 주입 시 실제로 반영됨을 확인(장식용 config 재발 방지).
  - **재검토 후 제외**: `stage3_fundamental_improve.py`의 `len(q_series) < 6`은 처음엔 `sga_lookback_quarters`와 연동 안 된 버그로 의심했으나, 재검토 결과 YoY 계산이 `q_series[0,1,4,5]`를 직접 참조하는 구조적 최소 요구치라 파라미터화 대상이 아님으로 최종 판단.
- → 그룹 B는 `config/params.yaml`, `core/metrics_utils.py`, `stages/stage2~5*.py`, `backtest/run_backtest.py` 반영 완료. 그룹 A는 다음 세션 진행.