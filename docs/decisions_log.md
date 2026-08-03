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