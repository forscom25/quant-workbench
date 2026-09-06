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
- → architecture.md, screening_criteria.md 반영 완료 **(2026-09-06 갱신: `global.ttm_denominator` 설정 추가로 분모가 "스냅샷 유지"만은 아니게 됨 — `latest_snapshot`이 기본값이고 `avg_4q`를 선택지로 구현. 최신 내용은 screening_criteria.md 참고.)**

### [결정] 탈락 종목 보존 정책
- **확정**: 종목을 완전히 버리지 않고, `pipeline.py`에서 단계별 통과 데이터프레임을 `history` 딕셔너리에 담아 최종 결과와 함께 반환하는 방식 채택 (강제 폐기나 복잡한 상태 객체 대신 단순 딕셔너리 축적)
- 사유: 사후 분석("이 종목이 왜 stage2에서 떨어졌는지") 및 디버깅 용이성 확보
- → pipeline.py 구현 완료. architecture.md 반영 완료

### [결정] DART 일일 호출량 관리
- **확정**: `loader.py`에 `dart_daily_limit` 카운터 구현. 한도 도달 시 `RuntimeError`로 스크리닝 즉시 중단 (조용히 `NOT_COMPUTABLE`로 새는 대신 명시적으로 실패시켜 원인 파악 쉽게)
- → loader.py 구현 완료. architecture.md 반영 완료 **(2026-09-06 갱신: 이 카운터가 프로세스 메모리에만 있어 재시작마다 0으로 리셋되는 바람에 실제 서버 한도를 초과시킨 장애가 발생 — `dart_call_state.json`에 날짜별로 영속화하도록 개선. 자세한 경위는 같은 날짜 항목 "실제 백테스트 3차 실행" 참고.)**

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
- → stage3, stage4, metrics_utils.py 개편 완료. architecture.md 및 params.yaml 반영 완료 **(2026-09-06 갱신: `apply_percentile_filter`는 이후 2026-09-01 `fail_reason` 컬럼 리팩터 때 stage3/4가 컷오프 계산을 각자 인라인으로 바꾸면서 더 이상 호출하지 않게 됨 — 지금은 import만 남은 죽은 코드. "재사용성 극대화" 의도와 달리 실제로는 재사용되고 있지 않음.)**

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
- → `loader.py` 반영 완료. **(2026-09-06 갱신: 실제 경로는 `data/.cache`가 아니라 `data/cache`(점 없음) — 이 항목이 처음부터 잘못 적혀 있었음. `.gitignore`에도 두 표기가 혼재해 실제 경로가 무시 대상에서 빠지는 버그가 있었고 같은 날 함께 수정함.)**

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

### [결정] params.yaml 하드코딩 전수점검 (그룹 A, B 모두 완료)
- **논의**: 사용자가 params.yaml에 정리할 게 더 있다고 지적(백테스트 시작/종료 날짜 등 예시). 전수 점검 결과 두 그룹으로 분류:
  - **그룹 A**: 백테스트 시작/종료 연도가 `run_backtest.py`(argparse 기본값), `cache_warmup.py`의 `get_quarterly_rebalance_dates(2019, 2025)`, `years = list(range(2018, 2026))` 세 곳에 각각 독립적으로 하드코딩되어 있어, 기간을 바꾸려면 3곳을 다 고쳐야 하고 하나라도 빠뜨리면 캐시 예열 범위와 백테스트 범위가 어긋나는 위험 존재.
  - **그룹 B**: Stage2 `effective_tax_rate`(0.22), Stage3/4/5의 `calc_zscore` 극단값 클리핑 분위(0.01/0.99), Stage5의 ICR 캡·클리핑·경고 태그 임계치 6종, `BacktestEngine`의 `fee_rate`/`slippage`/`min_portfolio_size`를 전부 params.yaml로 이관. 기본값은 기존 하드코딩 값과 동일하게 유지(회귀 없음)하고, 커스텀 값 주입 시 실제로 반영됨을 확인(장식용 config 재발 방지).
  - **재검토 후 제외**: `stage3_fundamental_improve.py`의 `len(q_series) < 6`은 처음엔 `sga_lookback_quarters`와 연동 안 된 버그로 의심했으나, 재검토 결과 YoY 계산이 `q_series[0,1,4,5]`를 직접 참조하는 구조적 최소 요구치라 파라미터화 대상이 아님으로 최종 판단.
- **그룹 A 처리**: `backtest.start_year`(2019)/`end_year`(2025)를 params.yaml에 신설해 단일 출처로 통합. `run_backtest.py`는 argparse `--start`/`--end` 기본값을 `None`으로 바꾸고, CLI 인자가 있으면 그걸 우선하되 없으면 params.yaml 값으로 폴백하도록 수정(기존 CLI 오버라이드 기능은 그대로 유지). `cache_warmup.py`의 `years` 범위(과거엔 `range(2018, 2026)`으로 독립 하드코딩)는 `range(start_year - 1, end_year + 1)`로 파생 계산하도록 변경 — `-1`은 시작 연도 첫 분기의 TTM 계산이 전년도 분기까지 참조하는 구조적 이유. 세 곳 모두 값이 일치하고 기존 하드코딩과 동일한 기본 동작을 내는지 합성 테스트로 검증 완료.
- → `config/params.yaml`, `core/metrics_utils.py`, `stages/stage2~5*.py`, `backtest/run_backtest.py`, `backtest/cache_warmup.py` 반영 완료. `screening_criteria.md` 반영 완료.

### [결정] DART 캐시 유효기간 분리 (실제 백테스트 실행 전 발견)
- **이슈**: 사용자가 "이제 백테스트 돌리면 될까?"라고 물어 실행 전 캐시 상태를 점검하다가, `data/cache/dart_*.csv`(21,768개)의 95.9%(20,884개)가 공용 `cache_days`(30일) 기준으로 만료 판정됨을 발견. 캐시 생성 시점(7/31~8/8)과 오늘(9/6) 사이가 30일을 넘었기 때문. 지금 그대로 백테스트를 실행하면 만료된 캐시를 전부 DART API로 재요청하려다 일일 호출 한도(9,500건, `endpoints.json`에 미설정이라 기본값 적용)를 하루 만에 못 채워 `RuntimeError`로 중단될 위험이 컸음.
- **원인 분석**: `cache_days=30`이 유니버스/시세/펀더멘털/DART 재무제표 4종 캐시에 공통 적용되는 구조였는데, DART 확정 공시 재무제표는 정정공시 등 드문 예외를 빼면 사실상 불변인 과거 데이터라 "30일 지나면 재요청"이라는 정책 자체가 안 맞았음.
- **해결책**: `QuantDataLoader`에 `dart_cache_days`(기본 3650일) 신설, `_is_cache_valid()`가 `cache_days`를 선택적으로 오버라이드받도록 수정. `get_financial_statements()`만 `dart_cache_days`를 명시적으로 전달하고, 나머지 3종 캐시는 기존 30일 정책 그대로 유지(유니버스 16개·시세 11개·펀더멘털 6개는 pykrx/FDR 호출이라 DART 같은 엄격한 일일 한도 이슈가 없어 그대로 둬도 무방하다고 판단).
- **검증**: 실제 캐시 파일 타임스탬프로 재계산한 결과, 신규 정책 적용 시 DART 캐시 만료 0건(0.0%)으로 확인.
- → `data/loader.py` 반영 완료. `screening_criteria.md` 반영 완료.

### [결정] Stage3 `na_reasons` 타입 버그 수정 (실제 백테스트 1차 실행 중 발견)
- **이슈**: `run_backtest.py` 실제 실행 중 `TypeError: list indices must be integers or slices, not str` 발생 (`stage3_fundamental_improve.py`의 `na_reasons['TURNAROUND_NOT_COMPUTABLE'] = (...)` 지점). `na_reasons`가 `[]`(list)로 초기화돼있는데 코드는 딕셔너리처럼 키로 대입하고 있었음 — 처음부터 있던 버그였으나, 그동안의 합성 테스트가 전부 결측 없는 "정상" 데이터만 줘서 `DATA_TOO_SHORT`/`TURNAROUND_NOT_COMPUTABLE` 분기(신규상장·금융업 등 GPM 계산 불가 종목) 자체를 타본 적이 없어 미발견 상태였음. 실제 DART 데이터로 처음 전체 백테스트를 돌리자마자 노출됨.
- **해결책**: `na_reasons = []` → `na_reasons = {}`로 초기화 변경 (스키마 정의 `TurnaroundMetrics.na_reasons: dict`와도 일치). 같은 패턴이 Stage1/2/4/5에도 있는지 전수 검색했으나 나머지는 전부 `.append()` 기반 list 사용이라 문제 없음을 확인.
- **교훈**: 합성 데이터 테스트는 "정상 경로"만 확인하고 결측치·예외 케이스가 실제로 존재하는 실데이터 없이는 이런 타입 불일치를 못 잡는다는 걸 재확인. 앞으로 유사 수정 시 결측/예외 분기를 의도적으로 트리거하는 케이스를 테스트에 포함할 것.
- → `stages/stage3_fundamental_improve.py` 반영 완료.

### [결정] 백테스트 실행 중단성 개선 (실제 백테스트 2차 실행 중 발견 — 네트워크 순단)
- **이슈**: 실행 재시도 중 `[FDR/pykrx 펀더멘털 호출 실패] 20190329: Connection aborted...`로 pykrx 호출이 순간 실패했고, `loader.get_market_fundamental_cross_section()`이 빈 `pd.DataFrame()`을 반환하자 `stage4_valuation.py`의 `fund_t0.columns.str.lower()`가 `AttributeError`로 죽으며 다년치 백테스트 전체가 중단됨. 근본적으로 이 프로젝트의 pykrx 호출부는 (DART와 달리) 재시도 로직이 전혀 없었고, `BacktestEngine.run()`의 분기별 루프도 예외 처리가 없어 어느 한 분기의 일시적 오류가 전체 실행을 죽이는 구조였음.
- **해결책** (3단):
  1. **재발 빈도 감소**: `endpoints.json`에 이미 있었으나 아무 데서도 안 읽히던 `KRX.max_retries`를 실제로 배선. `QuantDataLoader._fetch_with_retry()` 공용 헬퍼 신설, `get_kospi_universe`/`get_sector_metrics`/`get_market_fundamental_cross_section`의 pykrx 호출부에 전부 적용.
  2. **국소 방어**: `stage4_valuation.py`가 `fund_t0`/`fund_t4`가 빈 DataFrame으로 와도(재시도까지 소진된 경우) `.str` 접근자로 죽지 않고, 해당 분기 밸류에이션 지표를 결측 처리해 기존 PBR-NaN 구제(exempt) 경로로 자연히 흡수되도록 방어 코드 추가.
  3. **전체 방어(가장 중요)**: `BacktestEngine.run()`의 분기별 루프 전체(`pipeline.run()` + 벤치마크 수익률 계산)를 try/except로 감싸, 어느 한 분기에서 어떤 예외가 나든(네트워크 오류, 예기치 못한 데이터 이슈 등) 그 분기만 `skipped_low_count=True`로 현금(0%) 처리하고 나머지 분기는 계속 진행하도록 변경. 지금까지 스테이지3 버그와 이번 네트워크 순단, 두 번의 크래시 모두 "한 지점의 문제가 전체 6년치 백테스트를 날려버리는" 동일한 구조적 취약점이 원인이었음을 인지하고, 근본 원인(회복탄력성 부재)을 이번에 해결함.
- **검증**: (1) 인위적으로 2회 실패 후 성공하는 콜백으로 `_fetch_with_retry` 재시도/소진 동작 확인. (2) 빈 펀더멘털 DataFrame을 주입해 stage4가 크래시 없이 전원 구제 처리하는지 확인. (3) 특정 분기에서 예외를 강제 발생시켜, 그 분기만 현금 처리되고 전후 분기는 정상 처리되는지 확인 — 3가지 모두 합성 테스트로 통과.
- → `data/loader.py`, `stages/stage4_valuation.py`, `backtest/forward_return.py` 반영 완료.

### [결정] 실제 백테스트 3차 실행 — 조용한 22개 분기 스킵의 진짜 원인 확정 및 후속 조치
- **경과**: 위 회복탄력성 개선 이후 실제로 다시 실행했으나, `outputs/performance_log.csv`에서 27개 분기 중 2020-06-30 이후 22개(81%)가 `portfolio_return=0.0`이면서 `benchmark_return`까지 비어있는 패턴 발견 — `min_portfolio_size` 미달 스킵이라면 benchmark_return은 정상 계산됐을 것이므로, 매 분기 `pipeline.run()` 자체가 예외를 던지고 있었다는 신호로 판단. 사용자가 실제 로그에서 `[Rate Limit] DART API 일일 호출 한도(9500회)` 메시지를 확인해 DART 일일 호출 한도 소진이 원인임을 확정.
- **근본 원인 추가 규명**: `dart_call_count`가 프로세스 메모리에만 존재해 스크립트를 재시작할 때마다 0으로 리셋됨. 그날 stage3 버그·stage4 네트워크 오류로 죽은 시도들을 포함해 같은 날 여러 번 재시도하면서, 로컬 카운터는 매번 여유가 있다고 착각했지만 DART 서버 쪽 실제 누적 사용량은 계속 쌓여 실제 한도를 초과시킨 것으로 결론. (사용자가 직접 지적함)
- **해결책**: `QuantDataLoader`에 `_load_dart_call_count()`/`_persist_dart_call_count()`를 추가해, 오늘 날짜 기준 누적 호출 수를 `dart_call_state.json`에 영속화. 프로세스가 몇 번을 재시작하든 같은 날엔 카운트가 이어지고, 날짜가 바뀌면 자동으로 0부터 다시 센다. `use_cache=False`면 기존처럼 영속화를 건너뛰고 항상 0에서 시작.
- **검증**: pykrx 등 미설치 의존성을 더미 모듈로 스텁 처리해 `QuantDataLoader`를 부분 로드한 뒤, (1) 최초 실행 0에서 시작, (2) 같은 날 프로세스 재시작 시 이전 카운트(5000)를 이어받아 누적(8000)되는지, (3) 날짜가 바뀌면 0으로 리셋되는지 3가지 시나리오 모두 확인.
- **후속**: 사용자가 `cache_warmup.py`를 재실행해 27,008건 전량 수집/캐시 적중(데이터 없음 0건)으로 성공 완료 — 이제 실제 백테스트가 신선한 DART 호출을 거의 필요로 하지 않을 것으로 예상.
- → `data/loader.py` 반영 완료.

### [결정] 백테스트 산출물 파일명에 실행 시각 포함
- **이슈**: 오늘 하루에만 크래시·재시도로 `run_backtest.py`를 여러 번 실행했는데, `outputs/performance_log.csv`/`portfolio_log.csv`가 고정 파일명이라 매번 이전 결과가 조용히 덮어써짐 — 실행 이력이 하나도 안 남는 문제를 사용자가 지적.
- **해결책**: `run_backtest.py`가 저장 시 실행 시각(`YYYYMMDD_HHMMSS`, 초 단위까지)을 파일명에 포함하도록 변경(`performance_log_{timestamp}.csv`, `portfolio_log_{timestamp}.csv`) — 날짜만으로는 오늘 같은 날 여러 번 돌린 경우를 못 구분하므로 초 단위까지 포함. 이에 따라 `analysis/visualize.py`의 `__main__` 블록이 고정 파일명을 찾던 것도, `outputs/` 내 타임스탬프 파일 중 가장 최근 것을 자동으로 찾도록 함께 수정(파일명 정렬 순서가 시간 순서와 동일함을 이용).
- **참고**: 기존에 고정 파일명으로 저장돼 있던 `outputs/performance_log.csv`/`portfolio_log.csv`(22/27분기 비어있던 실패 실행분)는 새 glob 패턴(`performance_log_*.csv`)에 안 걸려 더 이상 자동 선택되지 않음 — 삭제하진 않았으니 필요 없으면 사용자가 직접 정리.
- **검증**: 임시 디렉터리에 서로 다른 시각의 더미 파일 3개를 만들어, 파일명 정렬 결과가 실제 시간 순서와 일치하고 가장 최근 실행분이 올바르게 선택되는지 확인.
- → `backtest/run_backtest.py`, `analysis/visualize.py` 반영 완료.

### [결정] 실제 백테스트 4차 실행 — DART 한도 재소진의 진짜 원인 확정(빈 응답 미캐싱 버그)
- **경과**: `cache_warmup.py` 재예열 성공(27,008건, 데이터없음 0건) 직후 `run_backtest.py`를 실행했으나 2020-06-30 처리 중 다시 `[Rate Limit]` `RuntimeError`로 즉시 중단(지난번 회복탄력성 개선 덕분에 22개 분기가 조용히 스킵되는 대신 정확한 지점에서 멈춤 — 의도한 대로 동작). 사용자가 "캐시가 있는데 왜?"라고 의문 제기.
- **원인 규명**: `dart_call_state.json`을 확인해 오늘 실제로 9,500회 DART 호출이 소진됐음을 확인. 캐시 디렉터리의 새 파일 수(2,959개)와 실제 소진 호출 수(9,500회)의 차이(~6,500회)에 주목해 `get_financial_statements`를 재검토한 결과, **DART가 빈 응답(개별재무제표만 있는 회사에 CFS를 요청하는 등 정상적인 무응답)을 줄 경우 캐시 파일을 전혀 생성하지 않고 매번 `None`만 반환**하는 버그를 발견. 이 때문에 같은 (ticker, year, report_code, fs_div) 조합이 여러 base_date의 TTM/op_margin 계산에서 반복 참조될 때마다 실시간 API를 낭비 호출하고 있었음 — `cache_warmup.py`가 CFS만 시도하고 성공 여부와 무관하게 `success_count`를 늘리는 방식이라 이 낭비를 사전에 드러내지 못했던 것도 확인.
- **해결책**: `get_financial_statements`에 `.empty` 마커 파일을 도입 — 빈 응답도 결과로 캐싱하되, 아주 최근 분기는 아직 미공시일 뿐일 수 있어 `dart_cache_days`(영구)가 아닌 일반 `cache_days`(기본 30일)만 적용해 추후 재확인 여지를 남김.
- **검증**: 같은 (ticker, year, report_code, CFS) 조합을 3회 반복 요청해도 실제 API 호출은 1회만 발생하고, 이후 성공하는 OFS 조합은 정상적으로 캐싱됨을 합성 테스트로 확인.
- **참고**: 이 수정과 무관하게 오늘 실제 DART 서버에 9,500회가 진짜로 소진됐으므로, 오늘 중 재시도는 여전히 불가 — 내일(한도 리셋 후) 재시도 시 이번 수정 덕분에 동일한 낭비 재발 없이 훨씬 적은 신규 호출로 완주할 것으로 예상.
- → `data/loader.py` 반영 완료.