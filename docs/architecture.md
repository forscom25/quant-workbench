# stock_screener 아키텍처 노트

> 5단계 필터링 파이프라인(소외 섹터 → 섹터 리더 → 펀더멘털 개선 → 밸류에이션 → 재무 건전성) 기반 종목 스크리너.
> 이 문서는 **항상 현재 확정된 설계 상태(What)**만 반영한다. 
> 전환 배경, 버그 수정 이력, 미결 항목 등은 [`docs/decisions_log.md`](./decisions_log.md) 참고.

## 1. 폴더 구조

```text
stock_screener/
├── config/
│   ├── endpoints.json      # API 관리 (rate limit, retry 설정)
│   └── params.yaml         # 모든 임계치·가중치 (하드코딩 금지)
├── data/
│   └── loader.py           # 시세/재무/섹터 원자료 수집·캐싱 (계산 없음)
├── stages/
│   ├── stage1_neglected_sector.py
│   ├── stage2_sector_leaders.py
│   ├── stage3_fundamental_improve.py
│   ├── stage4_valuation.py
│   └── stage5_financial_health.py
├── core/
│   ├── pipeline.py         # 단계 실행 오케스트레이터 (계산 없음)
│   ├── schema.py           # 데이터클래스 (입출력 표준 정의)
│   └── metrics_utils.py    # 재사용 가능한 순수 통계 함수 (Z-score, 합산 등)
├── backtest/
│   └── forward_return.py   # 각 단계별 신호 검증 및 포트폴리오 시뮬레이션
└── main.py                 # 최종 실행 진입점
```

## 2. 역할 분담 및 책임 경계

판단 기준: "이 로직이 `params.yaml`의 값이 바뀌면 결과가 달라지는가?"

- **Yes** → `stage`의 몫 (전략/기준 판단)
- **No**, 데이터 자체의 정합성 문제 → `loader`의 몫 (데이터 정제)

| 구분 | loader.py | pipeline.py | stages/*.py |
| :--- | :---: | :---: | :---: |
| 원본 수집/캐싱 및 point-in-time 검증 | ✅ | ❌ | ❌ |
| 시계열 조달 및 누적/단독 분기 정제 | ✅ | ❌ | ❌ |
| StockProfile 조립 및 Stage 순차 호출 | ❌ | ✅ | ❌ |
| 단계별 통과/탈락 종목 상태 이력(history) 관리 | ❌ | ✅ | ❌ |
| Z-score 산출 및 퍼센타일 등 비율 계산 | ❌ | ❌ | ✅ (metrics_utils 호출) |
| pass/fail 판정 및 구제(Exempt) 처리 | ❌ | ❌ | ✅ |

## 3. 핵심 계산 규칙 (현재 확정)

*   **유니버스 구성**: `pykrx` 기반 Point-in-Time 시총 및 섹터(`KRX-DESC`) 기준
*   **밸류에이션 (PBR/BPS)**: `pykrx` 기시산출값 직접 사용 (발행주식수 변동 이슈 우회)
*   **수익성 (ROE/ROIC/OPM)**: DART 원본 계정 사용 
    *   **분자(흐름)**: TTM (최근 4분기 합산)
    *   **분모(잔액)**: 최근 분기말 스냅샷
*   **분기 단독값 정제 (Isolation)**: 
    *   **IS/CF (손익/현금흐름):** 공시된 누적 데이터를 조달한 뒤, 직전 분기 누적치를 차감하여 해당 분기의 단독값을 산출하는 방식을 원칙으로 함.
    *   **BS (재무상태표):** 특정 시점의 잔액(스냅샷)이므로 차분 로직에서 제외.
*   **필터링 아키텍처**: 이진(Boolean) 절대 컷오프를 배제하고, `metrics_utils`를 활용한 **Z-score 가중 합산(Composite Score) 및 상위 N% 상대 평가** 원칙 적용 (Stage 1, 3, 4 공통).

## 4. `params.yaml` 현재 값 요약

| 단계 | 주요 파라미터 | 현재 설정값 |
|---|---|---|
| **Global** | 수익성 기준 / 공시 시차 검증 | `ttm` / `true` |
| **Stage 1** | 모멘텀 가중치 / 통과 비율 | 수익률(0.5), 거래대금(0.5) / 상위 40% |
| **Stage 2** | ROE, ROIC, OPM 변동성 컷오프 | 각 섹터 내 상위 50% |
| **Stage 3** | 매출/판관비/GPM 가중치 / 통과 비율 | 매출(0.4), 판관비(0.4), GPM(0.2) / 상위 30% |
| **Stage 4** | PBR/BPS 성장 가중치 / 통과 비율 | PBR(0.5), BPS(0.5) / 상위 30% |
| **Stage 5** | ICR / 부채비율 가중치 / 컷오프 비율 | ICR(0.7), 부채비율(0.3) / 하위 20% 컷오프 (상위 80% 통과) |

---

*참고: 기존 문서의 버그 수정 이력 및 미결 과제 항목은 [`docs/decisions_log.md`](./decisions_log.md)로 이관되었습니다.*
