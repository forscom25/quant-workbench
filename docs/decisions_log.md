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

## 2026-09-08

### [결정] 최초 완주 백테스트 결과 분석 및 상승장 미참여 문제 진단
- **배경**: 어제 수정 이후 백테스트가 처음으로 27개 분기 전부(스킵 0건) 완주. `dart_call_state.json` 확인 결과 오늘 7,567/9,500회 사용, 신규 CSV 1,563개 + 빈응답 마커 6,004개 = 7,567 정확히 일치 — 어제 고친 빈응답 캐싱 버그가 완전히 해소됐음을 재확인.
- **성과**: 누적수익률 전략 50.4% vs KOSPI 94.7%, CAGR 6.2% vs 10.4%, MDD -25.2% vs -32.3%(전략이 더 낮음), Sharpe 0.30, 분기 승률 40.7%. 하락장(2022-03, 2024-09 등)엔 전략이 지수를 큰 폭으로 앞섰으나, 2025년 하반기 두 분기에서만 30%p 넘게 뒤처져 누적 격차의 상당 부분을 설명.
- **사용자 가설**: "AI·전쟁 테마로 전력/반도체/방산이 코리안 디스카운트를 벗어나 파멸적 상승을 했고, 국장 특성상 한 번 불붙으면 극단적으로 움직인다"는 실제 배경 지식 제시. 검증 요청.
- **검증 1 (섹터 보유 이력)**: `portfolio_log`와 `universe_*.csv`의 섹터 매핑을 대조한 결과, 2019~2024년엔 분기당 0~1개씩 있던 전력/반도체/방산 종목이 2024-12~2025-09 4개 분기 연속 전무. 같은 기간 실제 주가(한화에어로스페이스 +239%, 두산에너빌리티 +257% 등, 전체 유니버스 중앙값 +10.1%)로 규모 확인.
- **검증 2 (Stage1 순위 추적)**: 시가총액가중 6개월 수익률로 전 섹터 순위를 재계산한 결과, "무기 및 총포탄 제조업"은 2021년 4분기~2025년 3분기까지 거의 매 분기 하위 10~30%(126개 섹터 중)에 머묾 — 사용자가 예상한 대로 `pass_ratio`를 완화하는 정도로는 구제 불가능한 만성적 극단치. 다만 "기타 전기장비 제조업"은 2024-12/2025-03/2025-06에 실제로 Stage1을 통과(순위 41/34/7위)했는데도 최종 포트폴리오엔 없어 Stage1이 유일한 원인이 아님을 시사.
- **검증 3 (실제 파이프라인 실행 — `quant` conda 환경 직접 사용)**: 이 세션의 Bash 도구가 기본으로 잡는 `python3`가 이 프로젝트와 무관한 다른 프로젝트("EmbeddedAI")의 가상환경이라 pykrx 등이 없었음 — `/opt/anaconda3/envs/quant/bin/python3` 인터프리터 경로를 직접 지정해 우회, 사용자의 실제 백테스트 환경과 동일한 조건에서 `pipeline.run()`을 재실행해 `history` dict를 티커 단위로 추적:
  - 티에이치엔(019180, 기타 전기장비): Stage1·2 통과, **Stage3에서 `composite_score_below_cutoff`**로 탈락. `sales_growth_yoy +19.2%`(양호)이지만 `sga_yoy_avg +28.6%`(매출성장보다 빠름)와 `gpm_yoy -3.4%p`(마진 하락)가 겹쳐 `stage3_score -0.153`.
  - LIG디펜스앤에어로스페이스(079550, 방산): **Stage1에서 `sector_not_qualified`**로 즉시 탈락(`return_z_score -2.92`) — 개별 기업 펀더멘털 평가 기회 자체가 없었음.
- **부수 발견**: `.env`의 `KRX_API_KEY`/`KRX_ID`/`KRX_PW`가 실제로 안 쓰이는 이유를 사용자가 질문 — 우리 코드(`self.krx_key`)는 정말 안 쓰지만, **`pykrx` 라이브러리 자체가 `KRX_ID`/`KRX_PW`로 자동 로그인을 시도**함을 `pykrx/website/comm/auth.py` 소스 확인으로 발견. 다만 `build_krx_session()`의 기본 인자값(`os.getenv(...)`)이 파이썬 함수 정의 시점(=`import pykrx` 시점)에 평가되는데, 이 프로젝트는 `import pykrx`보다 `load_dotenv()` 호출이 늦어(각 로더 인스턴스의 `__init__` 안에서 호출) 항상 로그인 실패함. 인증 없이도 기존 기능은 정상 동작해왔던 것으로 보여 당장 급한 이슈는 아니라고 판단, 다음 논의로 이관.
- → 분석 완료, 후속 결정은 아래 항목으로 이어짐.

### [결정] "테마주 vs 성장주" 구분 — Stage1/3 개선 방향 (A/B/C안 비교 후 A안 채택)
- **논의**: 사용자는 하락장 방어를 상승장 추종보다 우선시하는 철학을 명확히 하고, Stage3의 "판관비가 매출보다 빨리 늘면 감점" 로직 자체엔 동의(순수 테마주 배제 취지로 타당)하되, "테마주와 성장주를 어떻게 구분할 것인가"를 우려로 제기. 방산 사례(079550)는 Stage1이 개별 기업 펀더멘털을 전혀 안 보고 섹터 단위로 원천 배제한다는 점에서 더 심각한 문제로 재확인.
- **프레이밍**: 사용자가 이 방향을 "dual problem의 penalty 항 같다"고 표현 — 하드 제약(Stage1 통과/탈락)을 목적함수에 편입되는 페널티 항으로 완화하는 최적화 이론의 relaxation과 정확히 같은 발상임을 확인하고 그대로 채택.
- **B안(전 요인 통합 스코어링)의 함정**: 5단계 순차 하드컷을 없애고 전 요인(모멘텀+퀄리티+턴어라운드+밸류+재무건전성)을 한 번에 합산하는 방식이 가장 이상적이나, 지금 구조는 Stage2에서 탈락하면 Stage3~5 계산 자체를 생략해 DART 호출을 절약하는데(퍼널이 좁아질수록 계산량도 줄어듦), 통합 스코어링은 최종 합산을 위해 사전필터 통과 종목 전원에 대해 모든 요인을 계산해야 해서 이 절약 효과가 사라짐 — 어제 겨우 해결한 DART 일일 한도 문제가 재발할 위험이 크다고 판단해 보류.
- **A안(국소 조정) 채택**: (1) Stage1 `pass_ratio` 0.4→0.5로 완화(1차 검증용, 극단치는 못 잡을 것으로 예상하고 진행). (2) Stage3에 현금흐름 구제 신호 신설.
  - 설계: `loader.get_ttm_financials()`로 TTM OCF/순이익 조달(Stage5가 같은 티커에 대해 다시 조회하므로 대부분 캐시 재사용) → 매출성장(`sales_growth_yoy > cash_flow_rescue_min_sales_growth`, 기본 0%)과 현금흐름 건전성(`OCF>0`이고 `OCF≥순이익`)을 모두 만족하면 `CASH_FLOW_QUALITY_RESCUE` 태그.
  - 구현 시행착오: 처음엔 구제 대상을 컷오프 계산 모집단에서 미리 제외했는데, 이러면 컷오프 문턱값 자체가 달라져서 구제가 필요 없던 종목까지 영향을 받는 부작용 발견 → 정상적으로 랭킹에 참여시킨 뒤 컷오프 미달로 탈락 판정된 종목 중 구제 태그가 있는 것만 사후에 되살리는 방식으로 수정.
  - 검증: 재무수치(매출성장 +30%, 판관비 +208%, GPM -8.8%p)가 완전히 동일한 두 합성 종목에 현금흐름만 다르게 부여(OCF 150>순이익100 vs OCF -50)한 결과, 전자만 구제되고 후자는 탈락 유지됨을 확인. 매출 역성장 종목은 현금흐름이 좋아도 구제 안 됨도 함께 확인.
- **보류**: B안(통합 스코어링), Stage1의 "계산비용 게이트/모멘텀 신호 분리"(C안, A로 한계 확인되면 다음 후보)는 TODO로 이관.
- → `core/schema.py`(TurnaroundMetrics에 `ocf`/`net_income` 필드 추가), `stages/stage3_fundamental_improve.py`, `config/params.yaml`(`stage1_pass_ratio` 0.5, Stage3 `cash_flow_rescue_*` 신규) 반영 완료. `screening_criteria.md` 반영 완료.

## 2026-09-09

### [결정] A안 적용 후 5차 백테스트 — 성과 악화 발견 및 A/B 격리 실험
- **경과**: A안(Stage1 완화 + Stage3 현금흐름 구제) 반영 후 캐시 웜업·백테스트를 재실행(중간에 절전모드로 몇 차례 끊겼으나 데이터 손상 없이 27/27 분기 완주 확인). 결과가 직전(2026-09-08) 실행보다 전 지표에서 악화(누적수익률 50.4%→29.0%, MDD -25.2%→-31.5%, Sharpe 3.18→2.29) — 의도한 개선과 정반대로 나와 원인 파악 필요.
- **A/B 격리 설계**: 두 변경(pass_ratio 0.5, 구제 활성화)을 한 번에 켠 채로는 원인을 못 짚으므로, Config A(pass_ratio 0.5·구제 끔)와 Config B(pass_ratio 0.4·구제 켬)를 각각 단독 실행해 비교하기로 사용자 합의.
- **1차 결과(오해 소지 있었음)**: Config A는 baseline보다도 개선(누적 54.3%, MDD -25.1%, Sharpe 3.35). 그런데 Config B를 2026-09-08 baseline과 직접 비교했을 때 포트폴리오 구성이 27개 분기 중 26개에서 크게 달랐던 것은, 두 실행 사이에 캐시가 갈리며 실행 시점이 다른(전날 vs 당일) 데이터를 참조한 결과로, 이 비교 자체는 신뢰할 수 없었음 — 대신 같은 날 같은 캐시로 돌린 "Both ON"(0.5/구제O, 누적 29.0%) vs Config A(0.5/구제X, 누적 54.3%)는 pass_ratio를 고정한 순수 비교라 신뢰 가능했고, 이 비교만으로도 구제 로직이 성능을 깎는다는 결론은 유효했음.
- **구제 발동 여부 확인 시행착오**: "구제가 몇 건 발동했는지" 직접 `history['stage3']['na_reasons']`를 읽어 확인하려 했으나 최초 두 번의 시도 모두 "0건"으로 나와 모순 발생(구제 플래그를 켜고 끄는 것만으로 결과가 크게 달라지는데 발동 이력이 0건일 수 없음). 단일 프로세스 내에서 플래그만 바꿔가며 스테이지별 통과 인원을 직접 추적한 결과 Stage3에서만 이미 46→89명으로 갈리는 것을 확인했고, 이는 `na_reasons` 판독 쪽의 버그이지 구제 로직 자체의 문제가 아님을 특정.
- **근본 원인 발견(별도 버그)**: `core/pipeline.py`의 `_accumulate_results()`가 `fail_reason`만 특별 처리하고 있어, Stage2도 동일하게 `na_reasons`라는 이름의 컬럼을 쓰는 바람에 Stage3(및 Stage5)가 새로 계산한 `na_reasons`가 병합 시 조용히 버려지고 Stage2의 오래된 값이 그대로 남는 컬럼명 충돌 버그를 발견. Stage3의 `ocf`/`net_income`(A안에서 신규 추가된 필드)도 Stage5가 독자적으로 계산하는 동일 이름 컬럼과 같은 방식으로 충돌하고 있었음. 실제 통과/탈락 판정은 각 stage 내부의 로컬 `df`에서 이미 확정된 뒤 반환되므로 백테스트 성과 수치엔 영향이 없었지만, `history` dict를 통한 사후 감사(왜 이 종목이 통과/탈락했는지)가 Stage3부터 전부 무력화되어 있었던 심각한 투명성 결함.
- **해결책**: `_accumulate_results()`를 일반화 — `fail_reason`뿐 아니라 `ticker`를 제외하고 이름이 겹치는 모든 컬럼에 대해 이전 단계 값을 먼저 지우고 새 단계 결과가 항상 우선하도록 수정.
- **검증**: 수정 후 동일 분기(2021-09-30)를 재실행해 `history['stage3']['na_reasons']`가 실제 판정 근거(`TURNAROUND_NOT_COMPUTABLE`, `DATA_TOO_SHORT` 등)를 정확히 보여주고, `history['stage5']['ocf'/'net_income']`도 Stage3의 stale NaN이 아닌 Stage5 자체 계산값(실제 손익 수치)을 보여줌을 확인.
- **구제 로직의 진짜 문제 확정**: 버그 수정 후 재확인한 결과, pass_ratio=0.5(= "Both ON"과 동일 설정) 조건에서 2021-09-30 분기 하나만 봐도 Stage3 후보 134개 중 43개(32%)가 `CASH_FLOW_QUALITY_RESCUE`로 구제되어 통과 인원이 46→89명으로 거의 두 배가 됨. "매출성장률 0% 초과 + OCF≥순이익"이라는 기준이 의도(방산처럼 확정 수주 기반 성장주만 예외적으로 구제)와 달리 후보의 3분의 1가량을 무차별로 통과시키는, 지나치게 느슨한 필터였음이 실측으로 확정.
- **최종 결정**: `cash_flow_rescue_enabled: false`로 확정 비활성화(재설계 없이는 재활성화하지 않음). `stage1_pass_ratio: 0.5`는 Config A 단독 검증에서 성능 개선이 확인되어 그대로 유지.
- **부수 발견(별도 TODO로 이관)**: 위 조사 과정에서, Stage3/4의 컴포짓 스코어 하위 요소 중 하나가 (그 stage가 명시적으로 처리하는 예외 경로 밖에서) NaN이 되면 스코어 전체가 NaN이 되고, `NaN < cutoff` 비교가 항상 False라 `na_reasons`에 아무 태그도 없이 자동 통과되는 경로를 발견. 2021-09-30 샘플 기준 Stage3 134개 중 4개(3%), Stage4 46개 중 1개(2%) 수준으로 규모는 작지만, "왜 통과했는지 감사 불가능한" 종목이 소수 존재함 — 각 stage에 `SCORE_NOT_COMPUTABLE` 같은 명시적 태그를 추가하는 별도 후속 작업으로 보류.
- → `core/pipeline.py`(`_accumulate_results` 컬럼 충돌 수정), `config/params.yaml`(`cash_flow_rescue_enabled: false` 확정) 반영 완료.

## 2026-09-10

### [결정] TODO 우선순위 재검토 및 Tier 1(저위험·소규모) 항목 2건 해소
- **배경**: 어제 세션에서 쌓인 TODO 14개를 리스크·비용 기준으로 재분류. "지금 바로 처리할 가치가 있는" Tier 1로 (1) Stage3/4 NaN 자동통과 태깅, (2) 섹터-종목 기준일 역전(look-ahead bias) 방지 재도입 여부 결정 2건을 선정해 진행.
- **(1) NaN 자동통과 태깅**: `stages/stage3_fundamental_improve.py`와 `stages/stage4_valuation.py`에 `is_data_short`/`is_exempt`(또는 PBR 결측) 어느 쪽으로도 명시 처리되지 않았는데 컴포짓 스코어가 NaN인 행을 감지해 `na_reasons`에 `SCORE_NOT_COMPUTABLE` 태그를 남기고 기존 exempt 로직과 동일하게 컷오프 계산에서 제외하도록 수정. Stage4는 이 김에 그동안 아예 없었던 `na_reasons` 컬럼 자체를 신설(스키마에도 `ValuationMetrics.na_reasons` 추가)하고, 기존부터 있었지만 무태그였던 PBR 결측 구제 경로에도 `PBR_NOT_COMPUTABLE` 태그를 함께 부여.
- **검증**: 2021-09-30 분기로 파이프라인을 재실행해 (a) 태그 개수가 전날 진단 스크립트로 확인했던 수치(Stage3 4건, Stage4 1건)와 정확히 일치, (b) 최종 통과 종목 수(12개)와 구성이 수정 전과 동일함을 확인 — 필터링 로직은 그대로 두고 감사 가능성만 보강했음을 재확인.
- **(2) look-ahead bias 재도입 검토**: 결론부터 말하면 코드 추가 없이 TODO 종료. `core/pipeline.py`의 `run(base_date)`이 `get_kospi_universe(base_date)`와 `get_sector_metrics(base_date)`를 항상 동일한 `base_date` 인자로 호출하고, 두 함수 모두 내부적으로 같은 `_get_nearest_past_bday(base_date)`(숨은 전역 상태 없는 순수 함수)로 영업일을 해석함을 코드 추적으로 확인 — 섹터/종목 데이터가 서로 다른 기준일을 참조할 경로 자체가 현재 구조엔 없음. 게다가 섹터 데이터의 원천(pykrx 일별 시세·거래대금)은 DART 재무제표와 달리 거래일 종가가 확정되는 순간 그 시점 값으로 즉시 확정되는 데이터라 "공시 시차"라는 개념 자체가 적용되지 않는다. 옛 `inject_sector_info`(`StockProfile` 소속, 이미 삭제됨)가 막던 문제는 그 구 아키텍처에 국한된 것으로 결론짓고, 현재 구조에서는 방어할 실제 시나리오가 없다고 판단.
- → `core/schema.py`(`ValuationMetrics.na_reasons` 신규), `stages/stage3_fundamental_improve.py`, `stages/stage4_valuation.py` 반영 완료. `screening_criteria.md`(TODO 2건 종료 처리, 공통 설계 원칙 4번 갱신) 반영 완료.

### [결정] Tier 2 착수 — `na_reasons` 타입 stage 전체 통일
- **배경**: 어제 발견한 컬럼 병합 버그(2026-09-09)가 하필 `na_reasons`처럼 stage마다 타입이 다른(Stage3만 dict, 나머지는 comma-joined string) 컬럼에서 터졌던 만큼, 재발 방지 차원에서 타입부터 통일하기로 함. Tier 2로 분류했던 "Stage3 현금흐름 구제 재설계"보다 위험·비용이 훨씬 작아 먼저 처리.
- **조사**: `MetricStatus`/설명 문구(Stage3 dict의 튜플 두 번째 값)를 stage3 자체 외에 어디서도 다시 읽은 적이 없음을 전수 grep으로 확인 — 모든 소비처가 `.astype(str).str.contains('태그명')` 패턴만 사용. 즉 dict가 들고 있던 추가 정보는 write-only였고 실제로 손실 없이 단순화 가능.
- **해결책**: `core/schema.py`의 `TurnaroundMetrics.na_reasons`를 `dict` → `str`(comma-joined)로 변경. `stages/stage3_fundamental_improve.py`의 `na_reasons['KEY'] = (MetricStatus, msg)` 딕셔너리 대입 방식을 `na_reasons.append('KEY')` + `",".join(na_reasons)`로 전환(Stage2/5와 동일 패턴). 어제 추가한 `SCORE_NOT_COMPUTABLE` 사후 태깅도 딕셔너리 원소 대입 대신 문자열 이어붙이기로 변경. 더 이상 쓰이지 않는 `MetricStatus` import와 `core/schema.py`의 `field` import도 함께 정리.
- **검증**: 2021-09-30 분기 재실행으로 `na_reasons` 컬럼이 순수 `str` 타입임을 확인하고, 태그별 개수(SCORE_NOT_COMPUTABLE 4, DATA_TOO_SHORT 1, TURNAROUND_NOT_COMPUTABLE 4)와 최종 통과 종목 수(12개)가 리팩터 전과 완전히 동일함을 확인 — 순수 타입 정리였고 필터링 결과엔 영향 없음.
- → `core/schema.py`, `stages/stage3_fundamental_improve.py` 반영 완료. `screening_criteria.md`(TODO 종료 처리, 공통 설계 원칙 3번 갱신) 반영 완료.

### [결정] Stage3 현금흐름 구제 재설계 후 재검증 — 여전히 비활성화 유지로 최종 결론
- **배경**: Tier 2의 마지막 항목. 2026-09-09에 비활성화했던 현금흐름 구제를, 사용자와 두 축으로 재설계 방향을 논의: (1) 매출성장 임계치를 얼마나 올릴지 → 15% 초과로 결정(TODO에 이미 언급됐던 값, "업계 평균 대비 뚜렷한 고성장"). (2) 적자 기업에서 `OCF≥순이익` 조건이 무력화되는 문제 → `net_income`이 음수면 OCF가 조금만 양수여도 항상 조건을 만족해버려 이익의 질 검증이 사실상 안 되고 있었음을 발견, 사용자가 "적자 기업보다 흑자 기업이 좋은 성과를 낼 확률이 높다"는 논리로 흑자 기업(`net_income > 0`)만 구제 대상으로 좁히는 데 동의.
- **구현**: `stages/stage3_fundamental_improve.py`의 `cash_flow_healthy` 조건에 `net_income > 0`을 추가. `params.yaml`의 `cash_flow_rescue_min_sales_growth`를 0.0 → 0.15로 상향.
- **1차 검증(단일 분기)**: 2021-09-30 샘플로 재확인한 결과, 구제 대상 43개(우연히 구 버전과 동일 개수, 티커 자체가 같은 건 아님)가 실제로 전부 매출성장 15% 초과·순이익 흑자 조건을 만족함을 직접 수치로 확인(매출성장 최소 15.2%, 순이익 전원 양수) — 로직이 의도대로 정확히 동작.
- **2차 검증(전체 백테스트)**: 27개 분기 전체 재실행. 결과: 누적수익률 47.3%(구제 비활성화 baseline 54.3% 대비 낮음), CAGR 36.2%(vs 56.2%), MDD -24.7%(vs -25.1%, 근소하게 더 나음), Sharpe 3.18(vs 3.35), 승률 29.6%(vs 37.0%). 무차별 구제로 인한 심각한 악화(비교 대상: 구 버전 단독 실행 시 누적 29.0%)는 확실히 해소됐으나, 구제를 아예 끈 것보다는 전반적으로 소폭 낮은 성과.
- **최종 결정**: 사용자에게 두 결과를 제시하고 선택을 구함 — "숫자가 일관되게 없는 쪽이 나았다"는 이유로 구제 비활성화 유지를 최종 선택. `cash_flow_rescue_enabled: false`로 확정하되, 재설계된 로직과 기준값(`cash_flow_rescue_min_sales_growth: 0.15`, `net_income > 0` 조건)은 향후 다시 시도할 때 참고할 수 있도록 코드에 그대로 남겨둠.
- **참고**: MDD만큼은 재설계 버전이 baseline보다 근소하게 더 낮아(하락 방어 측면에서), 방어적 철학을 우선하면 재설계 버전도 완전히 근거 없는 선택은 아니었음 — 다만 누적수익률·Sharpe·승률이 전부 밀리는 폭이 MDD 개선폭보다 커서 비활성화 쪽으로 결론.
- → `config/params.yaml`(`cash_flow_rescue_enabled: false` 최종 확정, `cash_flow_rescue_min_sales_growth: 0.15`로 재설계값 보존), `stages/stage3_fundamental_improve.py`(`net_income > 0` 조건 추가) 반영 완료. `screening_criteria.md`(현금흐름 구제 절·TODO 갱신) 반영 완료.

### [결정] fail_reason / na_reasons 전체 태그 레퍼런스 문서화
- **배경**: "전체 파이프라인이 어느 정도 구축됐으니 탈락 이유를 지금 작성해볼까"라는 사용자 제안. 세 가지로 해석 가능해(실제 종목별 탈락사유 리포트 스크립트 / 태그 체계 문서화 / main.py 착수) 먼저 범위를 확인한 결과, 코드는 안 건드리고 태그 체계를 문서로 정리하는 쪽으로 확정.
- **작업**: `screening_criteria.md`에 각 stage 절 대신 한곳에서 조회할 수 있는 "부록: fail_reason / na_reasons 전체 레퍼런스" 절을 신설. `FailReason` Enum(5종), stage별 `na_reasons` 태그(9종), `warning_tags`/boolean 경고 필드(6종) 세 표로 정리했고, 문서만 보고 옮겨적지 않도록 전부 코드 grep으로 실제 존재 여부를 재검증.
- **부수 발견**: (1) `FailReason.CRITICAL_METRIC_NOT_COMPUTABLE`이 스키마에 정의만 되어 있고 실제 코드 어디에서도 쓰인 적이 없는 죽은 값임을 확인(전수 grep) — 당장 제거하진 않고 레퍼런스에 "미사용"으로 명시만 함. (2) Stage3/4 개별 절의 "업종 편차 주의" 목록이 최근 추가한 `SCORE_NOT_COMPUTABLE`(양쪽 다), `PBR_NOT_COMPUTABLE`(Stage4, 태그명 자체가 명시돼 있지 않았음)을 누락하고 있어 함께 보강.
- → `screening_criteria.md`(부록 신설, 목차 갱신, Stage3/4 "업종 편차 주의" 절 보강) 반영 완료. 코드 변경 없음.