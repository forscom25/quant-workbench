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
│   └── cache_warmup.py     # 백테스트 전용 DART API 스마트 캐시 예열 스크립트
├── analysis/               # [조회-읽기] 파이프라인/백테스트 산출물 분석 및 렌더링 (CQS 패턴)
│   ├── stats.py                # 순수 계산: Sharpe, MDD, 승률, 누적 수익률 등 (백테스트용)
│   ├── visualize.py            # stats.py 결과를 차트로 렌더링 (백테스트용)
│   ├── screening_stats.py      # 순수 계산: 단계별 깔때기 요약, 티커별 탈락사유 리포트 (실전 스크리닝용)
│   └── visualize_screening.py  # screening_stats.py 결과를 차트/CSV로 렌더링 (실전 스크리닝용)
├── outputs/                # 파생 산출물 저장 (Git 추적 제외)
├── scripts/
│   └── clean_cache.py      # 비영업일 기준으로 어긋난 캐시 파일 정리 유틸리티
├── debugging/               # 임시 조사·재현용 스크립트 모음 (2026-09-10, 예전 주피터 노트북 3개를 대체)
│   ├── smoke_test_loader.py    # QuantDataLoader 단위 스모크 테스트
│   ├── run_single_stage.py     # 소수 종목으로 stage 하나만 격리 실행
│   ├── trace_ticker.py         # 특정 종목이 어느 stage에서 왜 탈락/통과했는지 추적
│   └── inspect_raw_dart.py     # DART 원본 계정 테이블 덤프
└── main.py                 # 실전(기준일 지정 가능, 기본 오늘) 스크리닝 진입점. pipeline.run()을
                             # 1회 실행해 결과(최종 통과 종목 + 단계별 이력)를 outputs/에 저장만
                             # 하고(Command), 요약·시각화는 analysis/screening_*.py에 위임(Query).
```

## 2. 역할 분담 및 책임 경계

### 2.1. 파이프라인 계층 분리

판단 기준: "이 로직이 `params.yaml`의 값이 바뀌면 결과가 달라지는가?"

- **Yes** → `stage`의 몫 (전략/기준 판단)
- **No**, 데이터 자체의 정합성 문제 → `loader`의 몫 (데이터 정제)

| 구분 | loader.py | pipeline.py | stages/*.py |
| :--- | :---: | :---: | :---: |
| 원본 수집/캐싱 및 point-in-time 검증 | ✅ | ❌ | ❌ |
| 시계열 조달 및 누적/단독 분기 정제 | ✅ | ❌ | ❌ |
| DataFrame 조립 및 Stage 순차 호출 | ❌ | ✅ | ❌ |
| 단계별 통과/탈락 종목 상태 이력(history) 관리 | ❌ | ✅ | ❌ |
| Z-score 산출 및 퍼센타일 등 비율 계산 | ❌ | ❌ | ✅ (metrics_utils 호출) |
| pass/fail 판정 및 구제(Exempt) 처리 | ❌ | ❌ | ✅ |

각 stage는 탈락시킨 종목의 row도 지우지 않고 그대로 반환하되, 표준화된 사유
(`core/schema.py`의 `FailReason` Enum 값)를 담은 `fail_reason` 컬럼만 채워 넣는다.
`pipeline.py`는 `_accumulate_results`로 이전 단계 컬럼과 새 stage의 계산 결과를 ticker 기준으로
병합하고, `_filter_passed`로 `fail_reason`이 비어있는(=통과) 행만 추려 다음 단계로 넘긴다. 탈락 종목을
포함한 전체 결과는 매 단계 `history` dict에 그대로 남아 사후 분석("이 종목이 왜 몇 단계에서
떨어졌는지")에 쓰인다. 이 패턴은 Stage 1~5 전체에 동일하게 적용된다(Stage 1은 섹터 단위로 판정한 뒤
소속 티커에 `SECTOR_NOT_QUALIFIED` 사유를 상속시켜 티커 단위로 펼친다).
과거에는 `StockProfile`/`StageEvent` 기반의 클래스형 상태 머신으로 이력을 관리하는 설계였으나, 실제로
어떤 코드에서도 이 클래스들이 인스턴스화된 적이 없어 삭제되었다. 지금의 DataFrame + `fail_reason`
컬럼 방식이 유일하게 실제로 동작하는 이력 관리 메커니즘이다.

### 2.2. 실행 및 분석 계층 분리 (CQS 패턴)

명령-조회 분리(Command-Query Separation) 원칙에 따라 백테스트 실행과 분석 시각화를 엄격히 분리한다.

| 구분 | backtest/ | analysis/ |
| :--- | :---: | :---: |
| loader / pipeline 호출 및 의존성 | ✅ | ❌ (절대 참조 금지) |
| 산출물 결과 쓰기 (CSV 등 파일 생성) | ✅ | ❌ (읽기 전용) |
| S성과 지표(Sharpe, MDD 등) 통계 연산 | ❌ | ✅ (stats.py) |
| 차트 렌더링 (Matplotlib, Plotly) | ❌ | ✅ (visualize.py) |

## 3. 핵심 계산 규칙 (현재 확정)

*   **유니버스 구성**: `pykrx` 기반 Point-in-Time 시총 및 섹터(`KRX-DESC`) 기준
*   **밸류에이션 (PBR/BPS)**: `pykrx` 기시산출값 직접 사용 (발행주식수 변동 이슈 우회)
*   **수익성 (ROE/ROIC/OPM)**: DART 원본 계정 사용 
    *   **분자(흐름)**: TTM (최근 4분기 합산)
    *   **분모(잔액)**: 최근 분기말 스냅샷
*   **분기 단독값 정제 (Isolation)**: 
    *   **IS/CF (손익/현금흐름):** 공시된 누적 데이터를 조달한 뒤, 직전 분기 누적치를 차감하여 해당 분기의 단독값을 산출하는 방식을 원칙으로 함.
    *   **BS (재무상태표):** 특정 시점의 잔액(스냅샷)이므로 차분 로직에서 제외.
*   **필터링 아키텍처**: 이진(Boolean) 절대 컷오프를 배제하고, `metrics_utils`를 활용한 **Z-score 가중 합산(Composite Score) 및 상위 N% 상대 평가** 원칙 적용 (Stage 1, 3, 4, 5 공통). Stage 2만 예외적으로 ROE/ROIC/영업이익률 변동성 각각에 대한 독립적인 percentile 컷오프(boolean AND 결합)를 유지한다 — 세 지표를 하나의 합산 점수로 섞지 않고 각각을 유효성 게이트로 쓰는 설계이며, `screening_criteria.md` 2단계 참고.

## 4. `params.yaml` 현재 값 요약

| 단계 | 주요 파라미터 | 현재 설정값 |
|---|---|---|
| **Global** | 수익성 기준 / 공시 시차 검증 | `ttm` / `true` |
| **Stage 1** | 모멘텀 가중치 / 통과 비율 | 수익률(0.5), 거래대금(0.5) / 상위 40% |
| **Stage 2** | ROE, ROIC, OPM 변동성 컷오프 | 각 섹터 내 상위 50% |
| **Stage 3** | 매출/판관비/GPM 가중치 / 통과 비율 | 매출(0.6), 판관비(0.1), GPM(0.3) / 상위 30% (성장주 프리미엄 반영) |
| **Stage 4** | PBR/BPS 성장 가중치 / 통과 비율 | PBR(0.2), BPS(0.8) / 상위 30% (성장주 프리미엄 반영) |
| **Stage 5** | ICR / 부채비율 가중치 / 컷오프 비율 | ICR(0.7), 부채비율(0.3) / 하위 20% 컷오프 (상위 80% 통과) |

---

*참고: 기존 문서의 버그 수정 이력 및 미결 과제 항목은 [`docs/decisions_log.md`](./decisions_log.md)로 이관되었습니다.*
