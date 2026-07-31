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
- → pipeline.py 구현 완료. architecture.md TODO 체크 및 "결정 대기 중" 항목 제거 필요

### [결정] DART 일일 호출량 관리
- **확정**: `loader.py`에 `dart_daily_limit` 카운터 구현. 한도 도달 시 `RuntimeError`로 스크리닝 즉시 중단 (조용히 `NOT_COMPUTABLE`로 새는 대신 명시적으로 실패시켜 원인 파악 쉽게)
- → loader.py 구현 완료. architecture.md "알려진 한계" 표에서 해당 행 제거 필요

### [결정] 통계 계산 표본 수 부족 처리
- `op_margin_std` 등 계산 시 신규 상장주처럼 데이터가 부족한 경우, `metrics_utils.py`가 `NOT_COMPUTABLE` 상태를 부여
- Stage 필터링 단계에서는 이를 예외(exempt) 조건으로 구제하여 억울한 탈락 방지 — 이 원칙은 다른 stage의 유사 케이스(`ROIC_NOT_COMPUTABLE` 등)에도 동일하게 적용됨
- → screening_criteria.md 공통 설계 원칙(3-1) 및 각 stage 절 반영 완료

### [진행 중] op_margin_min_quarters 기본값 미정
- params.yaml에 넣을 최소 유효 분기 수 — 4로 할지 6으로 할지는 여전히 미확정, 백테스트 검증 필요 (구현은 완료되었으나 값 자체는 튜닝 대상)

### [결정] 분기 단독값 판별 방식
- 기존 `thstrm_nm` 텍스트 매칭("2분기", "3분기" 포함 여부)으로 누적/단독 판별 시도 → 보고서마다 문구 표기가 달라 신뢰 불가, 실제로 이중 차감 버그 발생 확인
- **확정**: `thstrm_add_amount` 컬럼 존재 여부를 우선 확인하고, 없으면 `thstrm_amount`를 기본값으로 캐스팅하는 try-except 구조로 `loader.py` 정제 로직 반영 완료
- ⚠️ **검증 필요**: `thstrm_add_amount`와 `thstrm_amount` 중 어느 쪽이 "누적"이고 어느 쪽이 "단독(3개월)"인지는 계정·보고서 종류에 따라 달라질 수 있음. 지난번 `thstrm_nm` 버그도 "이럴 것이다"라는 가정에서 시작됐던 만큼, 실제 raw 응답 샘플(반기·3분기 보고서의 매출액 등)로 두 컬럼 값을 직접 대조해 방향을 재확인해두는 걸 권장
- **관련**: BS(잔액) 항목은 애초에 차분 대상이 아님 — `sj_div`로 분기해서 BS는 스냅샷 그대로, IS/CF만 차분 적용
- → loader.py 구현 완료. architecture.md의 "구현 중 발견된 버그" 서술을 확정된 처리방식으로 갱신 필요
