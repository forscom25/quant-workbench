# 종목 선별 스크리닝 기준 (Screening Criteria)

이 문서는 5단계 종목 스크리닝 파이프라인의 평가 기준, 개선 근거, 스키마 매핑을 정리합니다.
기준은 백테스트 결과에 따라 계속 변경될 수 있는 **living document**입니다. 수정 시 하단 [변경 이력](#변경-이력)에 기록해주세요.

## 목차
- [1단계: 소외 섹터 발굴](#1단계-소외-섹터-발굴)
- [2단계: 섹터 내 우량주 탐색](#2단계-섹터-내-우량주-탐색)
- [3단계: 체질 개선 (Turnaround)](#3단계-체질-개선-turnaround)
- [4단계: 밸류에이션](#4단계-밸류에이션)
- [5단계: 재무 건전성](#5단계-재무-건전성)
- [공통 설계 원칙](#공통-설계-원칙)
- [부록: fail_reason / na_reasons 전체 레퍼런스](#부록-fail_reason--na_reasons-전체-레퍼런스)
- [미정 사항 (TODO)](#미정-사항-todo)
- [변경 이력](#변경-이력)

---

## 1단계: 소외 섹터 발굴

**목적**: 시장의 관심에서 벗어나 있는 섹터를 1차로 넓게 후보군에 포함시킨다. (Neglected Firm Effect)

| 항목 | 원본 기준 | 개선 기준 |
|---|---|---|
| 수익률 | 최근 6개월 수익률이 코스피 평균 대비 하위 20% | 섹터 초과수익률의 **z-score/percentile** (시가총액가중 섹터지수 기준) |
| 거래 소외 | 최근 1개월 평균 거래량이 1년 평균 대비 50% 이하 | **거래대금** 기준, **시장 전체 대비 비중 변화**로 정규화 |
| 통과 비율 | 하위 20% 절대 컷 | 넉넉하게 **상위 30~40%**를 2단계로 전달 |

**개선 근거**
- 하위 20% 절대 컷은 KRX 섹터 수(20~30개)를 고려하면 표본이 4~6개뿐이라 순위 노이즈에 취약함 → 연속값(z-score/percentile)으로 전환 
- 거래량(주식수) 기준은 저가주 밀집 섹터에서 착시 발생 → 거래대금 기준으로 전환 
- 시장 전체가 위축되는 시기엔 절대 거래량 감소가 "소외"를 의미하지 않음 → 시장 대비 비중 변화로 정규화 
- 실적발표 시즌, 명절 등 계절적 거래량 패턴 왜곡 방지 → YoY(전년 동기) 비교 병행 

**밸류트랩 최소 방어선**
- 낙폭 가속 필터: 최근 1개월 낙폭이 6개월 평균보다 더 가팔라지는 섹터는 `is_value_trap_warning = True`로 태깅 (구조적 쇠퇴와 일시적 소외를 최소한으로 구분) 

**스키마 매핑**: `SectorMetrics` (`return_z_score`, `volume_z_score`, `composite_score`, `is_value_trap_warning`, `fail_reason`) — 컷오프 미달 섹터도 row는 보존되고 `fail_reason`에 `COMPOSITE_SCORE_BELOW_CUTOFF`가 채워진다. 소속 티커에는 `pipeline.py`가 이 판정을 `SECTOR_NOT_QUALIFIED` 사유로 상속시켜 티커 단위 `fail_reason`을 만든다 (자세한 흐름은 [`architecture.md`](./architecture.md) §2.1 참고).

**알려진 한계 (섹터 라벨의 point-in-time 여부)**: `loader.get_kospi_universe()`가 종목별 `sector`를 채울 때 쓰는 `fdr.StockListing('KRX-DESC')`는 날짜 인자를 받지 않아 `base_date`와 무관하게 항상 "캐시 생성 시점"의 현재 업종 분류를 반환한다. 실제로 `universe_20190930.csv`와 `universe_20250930.csv`를 비교한 결과 공통 종목 747개 전부 섹터가 동일 — 즉 6년치 캐시 전부가 하나의 "오늘 기준" 업종 스냅샷을 공유하고 있음을 확인함(2026-09-06). 시가총액/종가/티커 목록은 `base_date` 기준 point-in-time이 맞지만 섹터만 예외. KRX 업종 재분류 자체가 드물게 일어나는 편이라 실무 영향은 제한적일 것으로 판단해, 과거 시점 업종 분류를 제공하는 별도 데이터 소스를 찾기 전까지는 한계로 남겨두고 넘어가기로 결정함.
**대체 소스 조사 완료, 한계 유지로 최종 결론 (2026-09-11)**: `pykrx.stock.get_market_sector_classifications(date, market)`(KRX 공식 "업종분류현황")가 `date` 인자로 실제 point-in-time 분류를 반환함을 라이브 호출로 확인했으나(2019-09-30 vs 2025-09-30 매핑이 실제로 다름), KOSPI 전체를 24~26개 대분류로만 나눠 현재 `KRX-DESC` 기준(~126개 세분류)보다 훨씬 거칠다 — 예를 들어 방산 5종목(LIG넥스원·풍산·한화에어로스페이스·한화시스템·현대로템)이 "금속"/"운송장비·부품"/"전기·전자"로 뿔뿔이 흩어져, Stage1이 원래 잡아내려는 "니치한 소외 섹터" 개념 자체가 사라진다. KRX 테마/업종 지수 구성종목(`get_index_portfolio_deposit_file`, 2014-05-02부터 조회 가능)도 point-in-time이지만 "K-AI 방산TOP5+"류 curated TOP-N 지수라 전체 커버리지가 안 됨. 무료로 "point-in-time + 현재 수준 세분화"를 동시에 만족하는 소스를 찾지 못해 한계 유지로 최종 결론.

**알려진 한계 (극단적 모멘텀 섹터의 구조적 배제, 2026-09-08 확인)**: 최근 6개월 수익률이 섹터 평균 대비 수 표준편차(예: z=-2.9) 벗어난 섹터는 `stage1_pass_ratio`를 아무리 완화해도(하드 컷오프인 이상) 통과가 사실상 불가능하다 — 실제로 "무기 및 총포탄 제조업"(방산) 섹터가 2021년 4분기~2025년 3분기까지 거의 매 분기 하위 10~30% 순위에 머물러 `sector_not_qualified`로 원천 배제됨을 실증 확인(방산은 이 기간 실제 주가도 KOSPI 중앙값의 5~25배 상승). 개별 기업 펀더멘털을 전혀 반영하지 않고 섹터 단위로 통째로 배제하는 구조라, 진짜 재평가받는 산업(정당한 성장)과 순수 테마를 구분할 기회 자체가 없다. 근본 해결(하드컷 → penalty 항 전환)은 계산 비용(DART 호출량) 문제와 얽혀 있어 보류 중 — decisions_log.md 2026-09-08 항목 참고.

---

## 2단계: 섹터 내 우량주 탐색

**목적**: 1단계에서 통과한 섹터 내에서, 매매 가능하고 실적이 안정적인 종목으로 후보를 좁힌다. 

| 항목 | 원본 기준 | 개선 기준 |
|---|---|---|
| 규모 | 섹터 내 시가총액 상위 30% 이내 | 시총은 **유동성 필터**로만 사용 (매매 가능성 확보 목적) |
| 퀄리티 | (없음) | **ROE/ROIC 섹터 내 상위 50%** 추가 |
| 안정성 | (없음) | **영업이익률 변동성(표준편차) 하위 50%** 추가 (실적 안정성) |

**개선 근거**
- "시총이 크다 = 우량하다"는 착시. 덩치만 크고 부실한 기업도 걸러지지 않음 
- 시총 단독 필터는 소외 섹터 안에서 "가장 안 소외된" 대형주만 걸러내 1단계 취지와 상충될 수 있음 
- 시총(유동성) + 수익성 지표(ROE/ROIC, 이익 변동성)를 별도 축으로 분리해 이중 필터로 운용 

**스키마 매핑**: `QualityMetrics` (`roe`, `roic`, `op_margin_std`, `na_reasons`, `fail_reason`) — ROE/ROIC/영업이익률 변동성 세 조건 중 하나라도(구제 대상 제외) 미달하면 `fail_reason`에 `QUALITY_CUTOFF_NOT_MET`이 채워지되 row는 보존된다.

**계산 방식**: `op_margin_std`는 TTM(이동합산)이 아닌 **최근 n개 분기(기본 8, `op_margin_lookback_q`)의 개별 분기 단독 영업이익률 시계열**로 계산한다 (TTM으로 만들면 변동성이 인위적으로 스무딩됨).  원자료는 `loader.get_quarterly_op_margin_series()`, 표준편차 계산은 `core/metrics_utils.compute_std()` 재사용 함수 사용 — 자세한 책임 분리는 [`architecture.md`](./architecture.md#loader--pipeline--stage-책임-경계) 참고.  유효 분기 수가 `op_margin_min_quarters`(기본 4) 미만이면 `NOT_COMPUTABLE`. 

**ROE/ROIC 계산 기준 (`global.profitability_basis`, 2026-09-11 도입)**: Stage2만 이 설정을 읽는다 — "수익성" 지표(ROE/ROIC)를 직접 다루는 유일한 단계이기 때문(Stage3/4/5는 이 설정과 무관하게 항상 TTM 사용).
- **`"ttm"`(기본값)**: 기존 동작 — 최근 4분기 합산(분자)/최근 분기말 스냅샷(분모)의 TTM 단일 시점 값.
- **`"annual"`**: 최근 `annual_trend_lookback_years`(기본 3)개년 확정 사업보고서(`loader.get_annual_financials_series()`)로부터 **"수준(최근년도 값) × `annual_level_weight` + 추세(최근년도-최고년도 변화량) × `annual_trend_weight`"**의 가중합산으로 계산(기본 0.5/0.5). 레벨과 추세가 같은 단위(ROE/ROIC 비율)라 Stage3/4/5의 composite score처럼 z-score화하지 않고 바로 가중평균한다. 설계 의도: "턴어라운드는 장기간 기반을 다진 뒤 오른다"는 철학에 맞춰, 이미 우량한 안정주(추세는 평평해도 수준이 높음)와 다년간 개선 중인 턴어라운드주(수준은 낮아도 추세가 가파름) 둘 다 보호하기 위함. 확정 연도가 1개년치뿐이면(신규상장 등) 추세 없이 수준값만 사용, 0개년치면 기존 `ROIC_NOT_COMPUTABLE` 등 결측 처리 경로로 자연히 흡수됨.
- Stage2와 Stage3(분기 YoY 턴어라운드 감지)의 역할 분담: Stage2(연간)="수익성의 기반이 탄탄한가"(장기 확인), Stage3(분기)="지금 가속 중이거나 반전되고 있는가"(단기 포착) — 두 단계가 서로 다른 시간 축을 보는 상호보완 구조.
- **기본값은 `"ttm"`으로 최종 확정 (2026-09-12 백테스트 A/B 검증 완료)**: `"annual"`은 누적수익률 -1.9%(vs ttm 54.3%), MDD -39.2%(vs -25.1%, 더 나쁨), Sharpe 0.01(vs 0.32)로 전 지표에서 명확히 열세. "장기 기반 확인이 더 안정적일 것"이라는 가설과 반대로 MDD까지 악화됨. 원인 추정: 확정 사업보고서는 회계연도 마감 후 3개월 뒤에야 공시되므로 최대 ~21개월 묵은 데이터로 "지금 우량한지"를 판단하게 되고, 그 사이 실적이 꺾인 종목까지 게이트를 통과시킴(annual 모드가 분기당 평균 14.9개 종목을 통과시켜 ttm의 12.0개보다 더 느슨) — 분기 노이즈를 줄이려던 의도가 시의성 손실로 상쇄된 것으로 해석. 코드는 남겨두되(cash_flow_rescue와 동일한 패턴) 채택하지 않음. 자세한 내용은 decisions_log.md 2026-09-12 항목 참고.

**업종 편차 주의 / na_reasons 태그**
- `OP_MARGIN_STD_NOT_COMPUTABLE`: 유효 분기 수(`op_margin_min_quarters`) 미달 (신규 상장주 등) → **구제(exempt) 통과**, 변동성 필터 미적용 
- `ROIC_NOT_COMPUTABLE`: 투하자본 계산 불가 (금융업/지주회사, 자본잠식 등) → **구제(exempt) 통과**, ROIC 필터 미적용 

---

## 3단계: 체질 개선 (Turnaround)

**목적**: 실적 펀더멘털이 실제로 개선되고 있는 종목인지 확인한다. 

| 항목 | 원본 기준 | 개선 기준 |
|---|---|---|
| 평가 방식 | 판관비 연속 감소 등 이진(Boolean) 절대 조건 | **지표별 Z-score 기반 가중 합산(Composite Score) 및 상위 N% 컷오프** |
| 판관비 | 최근 분기 연속 감소 | **판관비 증감률 YoY 평균의 Z-score화** (낮을수록 고득점) |
| 매출 성장 | 절대 역성장 시 무조건 탈락 | **매출 증감률 Z-score화** (역성장 시 감점되나 타 지표로 상쇄 가능) |
| 이익 품질 | GPM 개선 절대 조건 | **GPM 증감률 Z-score화** |
| 경고 태그 | (없음) | 매출이 임계치(`-5%` 등) 이하로 역성장 시 `is_cost_cutting_warning` 태그만 부여 |

**개선 근거**
- 이진(Boolean) 방식의 하드 컷오프(Hard Cut)는 "판관비는 소폭 증가했으나, 매출과 GPM이 압도적으로 성장한 우량주"를 억울하게 탈락시키는 병목(Bottleneck) 현상을 유발함.
- 지표별 상대 평가(Z-score) 후 합산 점수(Composite Score)로 상위 N%를 선별함으로써 시장 상황(호황/불황)에 유연하게 대응 가능.
- "제 살 깎기(매출은 박살 나는데 비용만 줄인 경우)"는 절대 탈락이 아닌 경고 태그(`is_cost_cutting_warning`)를 부여하여 포트폴리오 편입 시 비중 조절 등에 활용.

**스키마 매핑**: `TurnaroundMetrics` (`sga_yoy_avg`, `sales_growth_yoy`, `gpm_yoy`, `stage3_score`, `is_cost_cutting_warning`, `na_reasons`, `fail_reason`, `ocf`, `net_income`) — 컷오프 미달 시 `fail_reason`에 `COMPOSITE_SCORE_BELOW_CUTOFF`, 데이터 부족 시 `DATA_TOO_SHORT`가 채워지되 row는 보존된다.

**현금흐름 구제 (2026-09-08 도입, 2026-09-09 비활성화, 2026-09-10 재설계 후에도 비활성화 유지)**: 판관비 급증·GPM 하락으로 컷오프 미달 판정된 종목이라도, ① 실제 매출성장이 있고(`sales_growth_yoy > cash_flow_rescue_min_sales_growth`, 재설계 후 기본 15%) ② TTM 영업활동현금흐름(OCF)이 건실하고 흑자면(`OCF > 0`, `당기순이익 > 0`, `OCF ≥ 당기순이익`) `na_reasons`에 `CASH_FLOW_QUALITY_RESCUE` 태그를 남기고 구제하는 기능이 구현되어 있다. 배경: 확정 수주잔고 기반으로 설비·인력에 선제 투자하는 업종(예: 방산)은 매출 대비 비용이 일시적으로 빠르게 늘어 이 단계의 하드 컷에 걸리기 쉬운데, 그 지출이 실제 현금 유입으로 뒷받침되는지(진짜 성장 vs 순수 테마성 지출)로 구분하려는 의도였다. 최초 버전은 기준이 지나치게 느슨해(2021-09-30 샘플에서 Stage3 후보의 32%가 구제됨) 성과를 크게 악화시켰고, 매출성장 임계치를 15%로 올리고 적자 기업(구 버전은 `net_income`이 음수면 `OCF≥순이익` 조건이 사실상 항상 참이 되어 이익의 질 검증이 무력화됨)을 배제하도록 재설계한 뒤에도 — 무차별 구제 문제 자체는 해소됐지만(구제 대상이 실제로 15%↑·흑자 기업으로 좁혀짐을 확인) — 전체 백테스트 성과가 구제를 아예 끈 것보다 여전히 소폭 낮게 나와(누적수익률 47.3% vs 54.3%, MDD만 -24.7%로 근소 우위) **`cash_flow_rescue_enabled: false`로 최종 비활성화 확정**했다. 코드/스키마(`CASH_FLOW_QUALITY_RESCUE` 태그, `ocf`/`net_income` 필드, 재설계된 임계값)는 남겨두었다 — 자세한 배경은 decisions_log.md 2026-09-08/2026-09-09/2026-09-10 항목 참고.

**업종 편차 주의 / na_reasons 태그**
- `TURNAROUND_NOT_COMPUTABLE`: 재고자산·매출원가(GPM) 개념 자체가 없는 업종 (은행 등 금융업) → 결측치 처리되어 **점수 필터링 면제(구제 통과)** 
- `DATA_TOO_SHORT`: YoY 비교에 필요한 데이터 미달 → 업종 특성이 아닌 데이터 가용성 문제이므로 **기계적 탈락** (구제 대상 아님) 
- `CASH_FLOW_QUALITY_RESCUE`: 위 "현금흐름 구제" 참고 — 컷오프 미달이지만 매출성장+건전한 OCF로 구제됨
- `SCORE_NOT_COMPUTABLE` (2026-09-10 추가): 위 두 예외 경로 밖에서 세부 지표(매출/판관비/매출원가 등)가 결측돼 합산 점수 자체가 계산 불가 → **점수 필터링 면제(구제 통과)**. 태깅 전엔 `NaN < cutoff` 비교가 항상 False라 무태그로 조용히 통과했던 경로를 명시화한 것 — 실제 통과 여부는 동일, 감사 가능성만 확보

---

## 4단계: 밸류에이션

**목적**: 저평가 상태인지 확인하되, 정당한 저평가(밸류트랩)와 구분한다. 

| 항목 | 원본 기준 | 개선 기준 |
|---|---|---|
| 평가 방식 | PBR 하위 50% 및 BPS 절대 방어 조건 | **Z-score 기반 가중 합산(Composite Score) 및 상위 N% 컷오프** |
| PBR | 섹터 내 단순 순위 | **1/PBR (역수)의 Z-score화** (저PBR일수록 고득점) |
| 주당순자산 | BPS 역성장 시 무조건 탈락 | **BPS 성장률 YoY의 Z-score화** (역성장 시 감점) |
| 밸류트랩 | ROE 5% 미만 시 우선순위 하향 | 저PBR이나 ROE가 임계치 미달 시 `is_pbr_value_trap` **경고 태그 부여 (탈락 아님)** |

**개선 근거**
- BPS의 일시적 훼손(빅배스 등)이나 밸류트랩 의심 사유를 기계적 탈락 조건으로 두면, 일시적 부진을 겪고 턴어라운드 하는 유망 기업들이 100% 탈락하는 부작용 발생.
- PBR의 상대적 매력도와 BPS 훼손 여부를 합산 점수로 평가하여 유연성 확보.
- PBR이 0 이하(자본잠식)인 데이터는 Z-score 정규화 과정에서 클리핑(Clipping) 및 역수 처리 문제를 일으키므로, 결측치(`NaN`)로 치환하여 `EXEMPT` 처리 후 5단계로 넘김.

**스키마 매핑**: `ValuationMetrics` (`pbr`, `per`, `psr`, `bps_growth`, `stage4_score`, `is_pbr_value_trap`) — 필드명은 `bps_growth`(과거 `bps_growth_yoy`에서 변경). `per`/`psr` 둘 다 값만 보존하며 아직 composite score 계산에는 포함되지 않는다(참고용/향후 확장 필드, 반영 여부는 [TODO](#미정-사항-todo) 참고). `per`는 pykrx 응답에서 함께 실려오는 값을 그대로 쓰고, `psr`(시가총액/TTM매출)은 2026-09-11 도입 — TTM 매출은 pykrx에 없어 `loader.get_ttm_financials()`로 DART 원본을 직접 조회해 계산한다(같은 후보들에 대해 Stage3가 이미 같은 재무제표를 조회해둬 캐시가 예열되어 있는 경우가 대부분이라 신규 DART 호출 비용은 거의 없음).

**데이터 소스**: `pbr`/`bps`/`per`는 DART 계정을 조합해 직접 계산하지 않고 `pykrx.stock.get_market_fundamental()`의 point-in-time 기시산출값을 그대로 사용한다 (발행주식수 별도 조회 불필요).  근거는 [`architecture.md`](./architecture.md#밸류에이션-원자료는-pykrx-기시산출값-사용) 참고. 

**업종 편차 주의 / na_reasons 태그** (2026-09-10, Stage4에 `na_reasons` 컬럼 자체를 신설하며 태그명 명시)
- `PBR_NOT_COMPUTABLE`: PBR 미산출(`NaN`) 또는 0 이하(자본잠식 등) → **임시 구제(EXEMPT) 통과** — 최종 판단은 5단계 재무 건전성에서 자본잠식 여부로 걸러내는 쪽에 위임
- `SCORE_NOT_COMPUTABLE`: PBR은 정상이나 `bps_growth`(1년 전 BPS 결측 등) 쪽 결측으로 합산 점수 계산 불가 → **점수 필터링 면제(구제 통과)**. Stage3와 동일하게, 태깅 전엔 `NaN < cutoff` 비교가 항상 False라 무태그로 조용히 통과했던 경로

---

## 5단계: 재무 건전성

**목적**: 상환 능력과 이익의 실제 품질(현금 뒷받침 여부)을 종합적으로 평가하되, 성장을 위해 레버리지를 적극 활용하는 우량 기업이 억울하게 탈락하지 않도록 유연하게 필터링한다.

| 항목 | 원본 기준 | 개선 기준 |
|---|---|---|
| 평가 방식 | 부채비율 100% 이하 등 이진 컷오프 | **Z-score 기반 가중 합산(Composite Score) 및 풀(Pool) 상대 평가 (하위 20% 컷오프)** |
| 부채비율 | 업종 상대기준 컷오프 | **부채비율(역수) Z-score화 (가중치 30%)**. 부채비율 상위 10% 초과 시 `[과다부채]` 태그 부여 |
| 현금흐름 | OCF > 당기순이익 시 통과 | **OCF < 당기순이익 시 `[이익질주의]` 태그 부여 (탈락 아님)**[cite: 7] |
| 상환능력 | 이자보상배율 1.5배 컷오프 | **이자보상배율(ICR) Z-score화 (가중치 70%)**. 1.0 미만 시 `[ICR미달]` 태그 부여 |

**개선 근거**
- 폭발적인 자본 증식을 견인하는 기업은 R&D와 시설 투자로 인해 부채비율이 높을 수밖에 없음[cite: 7]. 빚의 크기보다 '압도적인 이익 창출력(ICR)'에 70%의 높은 가중치를 두어 유망 기업을 구제함
- 이진 컷오프는 턴어라운드 초기 기업을 무조건 탈락시키므로, 하위 20% 컷오프와 경고 태그(Warning Tag) 시스템으로 전환하여 최종 포트폴리오 비중 조절에 활용함
- 5단계까지 살아남은 종목들은 이미 각 섹터의 리더이므로, 섹터 내 비교 시 표본 부족(N=1)으로 인한 통계 오류가 발생함[cite: 7]. 따라서 최종 생존자 전체를 하나의 풀(Pool)로 묶어 상대 평가함

**스키마 매핑**: `FinancialHealthMetrics` (`debt_ratio`, `ocf`, `net_income`, `interest_coverage_ratio`, `stage5_score`, `warning_tags`, `na_reasons`, `fail_reason`) — 하위 20% 컷오프 미달 시 `fail_reason`에 `COMPOSITE_SCORE_BELOW_CUTOFF`가 채워지되 row는 보존된다 (다른 4개 단계와 동일한 패턴).

**업종 편차 주의 / na_reasons 태그**
- `DEBT_RATIO_CAUTION`: 금융업 등 부채비율 절대치 비교가 무의미한 업종 → 부채비율 기준 **면제**, 업종 내 상대비교로 대체 (구체 산출 방식은 [TODO](#미정-사항-todo) 참고, 아직 미확정) 
- `interest_coverage_ratio = inf`: 무차입이거나 이자비용이 0 이하인 초우량 상태(이자수익 > 이자비용) → `NOT_COMPUTABLE`이 아니라 "재무구조가 매우 건전해서 생기는 예외"이므로 **통과 처리** 

---

## 공통 설계 원칙

1. **절대 컷 대신 연속 점수**: z-score, percentile 사용을 원칙으로 하여 임계치 근처의 순위 노이즈에 흔들리지 않도록 함 
2. **반대 해석 가능성을 페어 조건으로 검증**: 각 단계의 핵심 지표는 "겉보기엔 좋아 보이지만 실제로는 반대 신호"일 위험이 있음.  예:
   - 소외 = 저평가 기회 vs 소외 = 구조적 쇠퇴 
   - 재고회전율 상승 = 효율화 vs 재고회전율 상승 = 수요 둔화 
   - PBR 낮음 = 저평가 vs PBR 낮음 = 정당한 평가(밸류트랩) 
   → 반드시 2차 조건을 페어로 걸어 방향성을 확인 
3. **업종 특성 편차는 `na_reasons`로 태깅**: 필드 타입은 단순하게(`float`) 유지하고, 결측/계산불가/해석주의 사유는 각 Metrics 클래스의 `na_reasons`에 개별 기록. **(2026-09-10 통일 완료)** Stage 2/3/4/5 전부 쉼표로 이어붙인 `str`(comma-joined tag string) 타입으로 통일했다 — Stage3만 `dict[str, tuple[MetricStatus, str]]`이었는데, `MetricStatus`/설명 문구를 어느 stage도 다시 읽은 적이 없어(항상 태그명 문자열 포함 여부만 확인) 손실 없이 단순화했다. 타입이 갈려있던 것이 `core/pipeline.py`의 컬럼 병합 버그(2026-09-09)를 더 찾기 어렵게 만든 원인이기도 했다.
   - `NOT_COMPUTABLE`: 계산 자체가 불가능한 경우 (예: 금융업 재고자산 없음) 
   - `CAUTION`: 계산은 되나 업종 특성상 절대비교가 부적합한 경우 (예: 금융업 부채비율) 
3-1. **`NOT_COMPUTABLE`이라고 해서 전부 탈락 처리하지 않는다** — 원인이 "업종 특성상 그 지표 자체가 성립하지 않음"인지 "데이터가 실제로 부족함"인지에 따라 처리가 갈린다: 
   - **업종/구조적 이유로 계산 불가** (예: 금융업 ROIC, 무차입 기업 이자보상배율): 해당 지표 필터만 **구제(exempt) 통과**, 나머지 지표로 판정 
   - **데이터 가용성 부족** (예: 신규상장으로 YoY 비교용 6분기치 미달인 `DATA_TOO_SHORT`): 구제 대상이 아니며 **기계적 탈락**.  판단 근거 자체가 없는 것과 업종 특성상 지표가 없는 것은 다르게 취급한다. 
   - 각 stage 문서의 "업종 편차 주의" 절에 구체 태그명과 처리 정책(구제/면제/기계적 탈락)을 명시한다. 
4. **Look-ahead bias 및 공시 시차(Disclosure Lag) 차단**: 캘린더 기준이 아닌 KOSPI 실제 영업일을 조회하여 기준일로 삼고(`loader._get_nearest_past_bday`), 재무 데이터는 아직 공시되지 않은 분기를 참조하지 않도록 동적으로 최신 공시 분기를 역산하여 `t=0` 시점으로 확정한다. ⚠️ 과거 이 문서는 "섹터 데이터 기준일이 종목 기준일보다 미래일 수 없도록 `inject_sector_info`의 `ValueError`로 스키마 레벨에서 강제한다"고 서술했으나, 이 메서드가 속해 있던 `StockProfile` 클래스 자체가 실제로 어디서도 쓰인 적이 없어 삭제되었다. **(2026-09-10 재검토 완료)** 현재 아키텍처에서는 `pipeline.run(base_date)`이 `get_kospi_universe(base_date)`와 `get_sector_metrics(base_date)`를 **동일한 `base_date` 인자로 직접 호출**하고, 두 함수 모두 내부적으로 같은 `_get_nearest_past_bday(base_date)`(순수 함수, 숨은 전역 상태 없음)로 영업일을 해석하므로 섹터/종목 데이터가 서로 다른 시점을 참조할 경로 자체가 구조적으로 없음을 코드 추적으로 확인했다. 또한 섹터 데이터의 원천(pykrx 시세/거래대금)은 DART 재무제표와 달리 거래일 종가 확정 즉시 그 시점 데이터로 확정되는 값이라 "공시 시차" 개념 자체가 적용되지 않는다 — `inject_sector_info`가 막던 문제는 이제 존재하지 않는 옛 아키텍처(`StockProfile`)에 국한된 것으로 결론짓고, 별도 체크 재도입은 불필요로 판단해 TODO에서 제거한다.
5. **forward-return 백테스트로 사후 검증**: 각 단계의 임계치(percentile 컷, 통과 비율, 가중치 w1/w2 등)는 최종 확정값이 아니라 백테스트를 통해 지속적으로 튜닝되어야 함. 검증 시 현실적인 마찰 비용(수수료, 슬리피지)과 T+1일 시가/종가 진입을 엔진에서 엄격히 차감하여 실전 수익률과의 괴리를 최소화한다.
6. **단계별 지표 누적 및 의존성 보존**: 각 단계의 필터링은 이전 단계에서 산출된 핵심 지표를 페어(Pair) 검증에 적극 활용합니다.  예를 들어 4단계(밸류에이션)의 밸류트랩 검증(`is_pbr_value_trap`)은 2단계에서 계산된 roe 지표를 필수로 요구합니다.  따라서 파이프라인은 통과 종목을 걸러내는 것뿐만 아니라, 각 단계에서 계산된 새로운 지표들이 다음 단계로 누락 없이 병합(Merge)되어 전달되도록 상태를 보존해야 합니다.
7. **실행(Command)과 조회(Query)의 엄격한 분리 (CQS)**: 파이프라인의 다중 시점 실행 및 데이터 쓰기(백테스트 CSV 생성) 환경과, 생성된 데이터를 읽기만 하여 통계/차트를 도출하는 성과 분석 환경을 철저히 분리하여 순수 함수 기반의 견고한 분석 아키텍처를 유지한다.
8. **자본 복리 증식 극대화 (성장주 프리미엄)**: 장기적인 관점에서 자본의 팽창 속도를 극대화하여 경제적 자유 달성 시점을 앞당기기 위해, 성장이 정체된 가치주나 무거운 배당주보다는 압도적인 외형 성장(Top-line)과 장부가가 빠르게 우상향하는 턴어라운드 성장주에 가중치 프리미엄을 부여한다.

---

## 부록: fail_reason / na_reasons 전체 레퍼런스

각 stage 절에 흩어져 있는 태그를 한곳에서 조회하기 위한 참조표(2026-09-10 작성). 개별 태그의 배경/처리 근거는 위 해당 stage 절과 [`core/schema.py`](../stock_screener/core/schema.py)를 우선 참고하고, 이 표는 "지금 실제 코드에 존재하는 태그가 무엇인지" 확인하는 용도로 쓴다 — 전부 코드를 직접 grep해 검증했다.

### `fail_reason` (row 단위 최종 통과/탈락 판정, `FailReason` Enum)

| 값 | 설정 주체 | 의미 |
|---|---|---|
| `sector_not_qualified` | `pipeline.py` (Stage1 섹터 판정을 소속 티커에 상속) | 소속 섹터 자체가 Stage1 컷오프 미달 — 개별 기업 펀더멘털은 전혀 평가되지 않고 탈락 |
| `quality_cutoff_not_met` | Stage2 | ROE/ROIC/영업이익률변동성 중 (구제되지 않은) 조건 미달 |
| `composite_score_below_cutoff` | Stage1(섹터 단위), Stage3, Stage4, Stage5 | 합산 Z-score가 해당 단계 상위 N% 컷오프 미달 — 4개 단계가 공유하는 가장 흔한 사유 |
| `data_too_short` | Stage3 | YoY 비교용 6개 분기 데이터 미달(신규상장 등), 구제 대상 아님 |
| `critical_metric_not_computable` | (없음) | 스키마에 정의만 되어 있고 실제 코드 어디에서도 쓰이지 않는 죽은 값(2026-09-10 전수 grep으로 확인) |

`fail_reason`이 `None`(결측)이면 해당 단계 통과, 값이 채워지면 탈락이며 row 자체는 삭제되지 않고 `history` dict에 보존된다(사후 감사용).

### `na_reasons` (탈락 사유가 아닌, "판정 근거"를 보조 설명하는 태그. Stage 2/3/4/5 전부 comma-joined string)

| Stage | 태그 | 의미 | 처리 |
|---|---|---|---|
| 2 | `OP_MARGIN_STD_NOT_COMPUTABLE` | 영업이익률 변동성 계산용 유효 분기(`op_margin_min_quarters`, 기본 4) 미달 | 구제(exempt) — 변동성 필터만 면제 |
| 2 | `ROIC_NOT_COMPUTABLE` | 투하자본 계산 불가(금융업/지주사, 자본잠식 등) | 구제(exempt) — ROIC 필터만 면제 |
| 3 | `TURNAROUND_NOT_COMPUTABLE` | GPM 계산 불가 업종(매출원가 개념 없는 금융업 등) | 구제(exempt) — 점수 NaN 처리, 컷오프 계산 대상서 제외 |
| 3 | `DATA_TOO_SHORT` | YoY 비교용 6개 분기 데이터 미달 | **기계적 탈락**(구제 대상 아님) — `fail_reason`도 동일 값으로 채워짐 |
| 3 | `CASH_FLOW_QUALITY_RESCUE` | 컷오프 미달이나 매출성장(`cash_flow_rescue_min_sales_growth` 초과, 재설계 후 15%)+흑자+건전한 OCF로 구제 대상 | 구제(fail_reason을 `None`으로 되살림) — **현재 `cash_flow_rescue_enabled: false`라 비활성, 태그 자체가 안 붙음**(2026-09-10 항목 참고) |
| 3 | `SCORE_NOT_COMPUTABLE` | 위 두 경로 밖의 결측(매출/판관비/매출원가 등)으로 합산 점수 계산 불가 | 구제(exempt) |
| 4 | `PBR_NOT_COMPUTABLE` | PBR 미산출 또는 0 이하(자본잠식 등) | 구제(exempt) — 최종 판단은 5단계 자본잠식 재검증에 위임 |
| 4 | `SCORE_NOT_COMPUTABLE` | PBR은 정상이나 BPS 성장률 등 결측으로 점수 계산 불가 | 구제(exempt) |
| 5 | `FINANCE_SECTOR_CAUTION` | 금융/증권/보험/은행/지주 섹터 — 부채비율 절대비교 부적합 | 부채비율 요소 제외, ICR 단독 점수로 대체(탈락 아님) |

### `warning_tags` 및 boolean 경고 필드 (통과/탈락과 무관, 포트폴리오 구성 시 참고용)

`na_reasons`/`fail_reason`과 달리 스크리닝 판정 자체엔 전혀 영향을 주지 않고, 최종 편입 후 비중 조절 등에 참고하라고 남기는 신호다.

| Stage | 필드/태그 | 발동 조건 |
|---|---|---|
| 1 | `is_value_trap_warning` (bool) | 최근 1개월 낙폭이 6개월 평균 월간 낙폭보다 더 가팔라짐(낙폭 가속) |
| 3 | `is_cost_cutting_warning` (bool) | 매출성장률이 `cost_cutting_only_sales_decline_threshold`(기본 -5%) 이하로 역성장 |
| 4 | `is_pbr_value_trap` (bool) | 저PBR로 고득점(`stage4_score > 0`)인데 ROE가 `value_trap_roe_threshold`(기본 5%) 미만 |
| 5 | `[ICR미달]` | `interest_coverage_ratio < icr_warning_threshold`(기본 1.0) |
| 5 | `[이익질주의]` | `OCF < 당기순이익` (이익이 현금으로 뒷받침되지 않음) |
| 5 | `[과다부채]` | 비금융 섹터 한정, 부채비율이 섹터 내 상위 `debt_ratio_warning_percentile`(기본 90%ile) 초과 |

---

## 미정 사항 (TODO)

- [ ] 1단계 z_return, z_volume 가중치(w1, w2) 초기값 0.5/0.5 → forward-return 상관관계로 백테스트 튜닝 
- [ ] 1단계 통과 비율 확정 → 2026-09-08에 0.4→0.5로 1차 완화, 2026-09-09 단독 A/B 테스트로 성능 개선 확인되어 0.5로 최종 반영. 극단적 모멘텀 섹터(예: 방산)는 이 정도 완화로는 구제 안 될 것으로 예상 — 위 "알려진 한계" 참고
- [ ] Stage1의 섹터 모멘텀 하드컷을 penalty 항(음의 가중치를 가진 점수)으로 전환할지 검토 — 개별 기업 펀더멘털이 뛰어나면 섹터가 과열이어도 만회할 수 있게 하는 방향. 다만 Stage1은 DART 호출량을 줄이는 계산비용 관문 역할도 겸하고 있어(현재는 후보군이 좁아질수록 이후 단계 계산량도 줄어드는 구조), 전 요인 통합 스코어링으로 가면 그 절약 효과가 사라져 DART 일일 한도 문제가 재발할 위험 큼 — 비용 관문(유동성/시총 등 모멘텀과 무관한 기준)과 모멘텀 신호를 분리하는 절충안(2026-09-08 논의에서의 "C안")부터 검토 예정
- [x] 재무 데이터 자체의 지연(발표 시점 lag)을 추적하는 필드 추가 — 2026-09-11, `QuantDataLoader.disclosure_lag_log`(인스턴스 속성, 리스트)로 구현. `parse_standardized_financials`가 `rcept_dt`를 계산할 때마다 `{ticker, year, report_code, base_date, rcept_dt, lag_days, accepted}`를 append만 함 — 판정에 쓰는 `standard_metrics` 계정값 dict와는 완전히 분리된 채널이라 필터링/스코어링에는 전혀 영향 없는 순수 감사용 메타데이터. (과거 문구에 있던 `sector_data_lag_days`는 실제 코드에 존재한 적 없는 문서 오기로 확인, 이번에 정정) 
- [ ] 5단계 부채비율 업종 상대기준의 구체적 산출 방식(업종 중위값 대비 몇 %인지) 확정
- [ ] 3, 4단계 성장주 타겟팅 세부 가중치(매출 0.6 / BPS 0.8 등) 반영 후, 편입 종목 수에 따른 컷오프 상향(상위 30% -> 20%) 튜닝 검토
- [ ] Stage4 `per` 필드를 composite score에 실제로 반영할지 여부 결정 (현재는 값만 보존, 스코어링 미반영)
- [x] `psr`(시가총액/TTM매출) 계산 로직 구현 — 2026-09-11, `ValuationMetrics.psr` 필드 신설 및 `stage4_valuation.py`에서 `loader.get_ttm_financials()`로 TTM 매출 조회 후 `market_cap/ttm_revenue`로 계산. TODO의 "Stage5급 공수" 추정과 달리 실제로는 Stage3가 같은 (ticker, base_date)에 대해 이미 분기별 재무제표를 조회해둬서 DART 원본 캐시가 예열되어 있었고, 2025-09-30 기준 실제 검증 시 신규 DART API 호출 0건으로 확인됨. `per`과 동일하게 값만 보존하고 composite score에는 아직 미반영(반영 여부는 별도 TODO, 아래 "Stage4 `per` 필드..." 항목과 함께 백테스트로 검증 필요)
- [x] `na_reasons` 필드 타입을 stage 전체에서 통일 — 2026-09-10, 전부 comma-joined string으로 통일(자세한 내용은 위 공통 설계 원칙 3번 항목 참고)
- [x] Stage3 현금흐름 구제(`cash_flow_rescue_enabled`) 재설계 — 2026-09-10, 매출성장 임계치 15% 상향 + 적자 기업 배제로 재설계 후 재검증. 무차별 구제 문제는 해소됐으나 백테스트 성과가 여전히 구제 완전 비활성화보다 낮아(누적 47.3% vs 54.3%) 최종 비활성화 유지로 결론(자세한 내용은 위 "현금흐름 구제" 절 참고). 향후 재시도 시 참고할 수 있도록 재설계된 코드/기준값은 남겨둠
- [x] Stage3/4의 컴포짓 스코어가 (명시적 예외 처리 밖의) 결측치로 인해 NaN이 될 때, `na_reasons` 태그 없이 조용히 자동 통과되는 경로 확인(2026-09-09, 2021-09-30 샘플 기준 Stage3 3%·Stage4 2% 규모) — 2026-09-10, 각 stage에 `SCORE_NOT_COMPUTABLE` 명시 태그 추가로 해결(Stage4는 기존에 없던 `na_reasons` 컬럼과 `PBR_NOT_COMPUTABLE` 태그도 함께 신설). 실제 통과 종목 수·구성에는 변화 없음을 확인(태깅만 추가, 필터링 로직은 기존과 동일)
- [x] 섹터-종목 기준일 역전(look-ahead bias) 방지용 명시적 체크 재도입 여부 결정 — 2026-09-10 코드 추적으로 재검토 완료, 별도 체크 불필요로 결론(자세한 근거는 위 "공통 설계 원칙" 4번 항목 참고)
- [x] `main.py`(실전 스크리닝 진입점) 착수 — 2026-09-10, `pipeline.run(base_date)`을 1회 실행해 결과 저장만 담당(Command), 깔때기 요약·티커별 탈락사유 리포트·시각화는 신설된 `analysis/screening_stats.py`·`visualize_screening.py`에 위임(Query) — `backtest/run_backtest.py` + `analysis/visualize.py`와 동일한 CQS 패턴. 실제 캐시된 과거 기준일(2025-09-30)로 종단 검증(847→339→150→56→18→14, 최종 통과 14개) 완료
- [x] 섹터 라벨을 point-in-time으로 만들 과거 시점 업종 분류 데이터 소스 조사 — 2026-09-11, 무료 대체재(KRX 공식 업종분류현황, KRX 테마지수 구성종목) 조사 완료했으나 둘 다 세분화·커버리지 문제로 채택 불가, 한계 유지로 최종 결론(자세한 내용은 1단계 "알려진 한계" 절 참고)
- [x] `global.profitability_basis`의 `"annual"`(연간 확정치 기준) 경로 구현 — 2026-09-11, Stage2 ROE/ROIC에 한해 "최근 N개년 수준+추세 가중블렌딩" 방식으로 구현(자세한 내용은 2단계 절 참고). 신규 `loader.get_annual_financials_series()`, `stage2_sector_leaders.annual_trend_lookback_years`/`annual_level_weight`/`annual_trend_weight` 파라미터 추가
- [x] `profitability_basis: "annual"`이 `"ttm"`(기존 기본값) 대비 실제로 더 나은지 백테스트 A/B 검증 — 2026-09-12, 27개 분기 전체 실행 결과 `"annual"`이 전 지표(누적수익률/MDD/Sharpe/승률)에서 명확히 열세로 확인되어 `"ttm"` 기본값 최종 확정. `annual_level_weight`/`annual_trend_weight` 튜닝은 큰 폭의 성과 격차를 메우기엔 부족해 보여 보류(2단계 절 참고)
- [x] `data/loader.py`의 DART/KRX 호출부에 타임아웃 실제 적용 — 2026-09-11, `annual` 백테스트 검증 시도 중 `requests.get()`에 `timeout` 미지정으로 서버 무응답 시 무기한 행(hang)되는 버그 발견(macOS `sample`로 스택 확인: `_ssl__SSLSocket_read` 블록, 7시간+ 무응답). `socket.setdefaulttimeout()`은 `requests`(urllib3)가 무시함을 실측으로 확인(블랙홀 IP 테스트에서 무시되고 75초 뒤 OS 레벨 타임아웃으로만 실패) — 대신 `requests.Session.request`를 몽키패치해 호출부가 `timeout` 미지정 시에만 기본값(`endpoints.json`의 DART/KRX `timeout` 중 큰 값) 주입하는 방식으로 해결. pykrx 호출부도 동시에 보호됨(부수 효과). 로컬 hang 서버 재현 테스트 및 `smoke_test_loader.py` 전체 통과로 검증 완료
- [ ] `global.ttm_denominator` 기본값을 `latest_snapshot` → `avg_4q`로 바꿀지 백테스트로 비교 검증 (avg_4q는 4개 분기 BS 값이 모두 있어야 해서 결측 종목이 늘어날 수 있는 trade-off 존재)

---

## 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-07-28 | 최초 작성.  5단계 기준 및 개선 근거 정리 |
| 2026-07-31 | 2단계 `op_margin_std` 계산 방식(분기 단독값 + metrics_utils) 명시.  4단계 `pbr`/`bps` 데이터 소스를 pykrx 기시산출값으로 확정, architecture.md 참조 링크 추가  |
| 2026-07-31 | Stage 1~5 구현 완료에 따라 각 단계 `na_reasons` 태그명(`OP_MARGIN_STD_NOT_COMPUTABLE`, `TURNAROUND_NOT_COMPUTABLE`, `DATA_TOO_SHORT`, `is_pbr_value_trap`, `DEBT_RATIO_CAUTION` 등)과 구제(exempt)/면제/기계적 탈락 처리 정책 구체화.  공통 설계 원칙에 `NOT_COMPUTABLE` 처리 분기 기준(업종 특성 vs 데이터 부족) 추가  |
| 2026-08-01 | 실전 KOSPI 데이터 Funnel 분석 결과 반영: 3단계 판관비율 확인 기간(sga_lookback_quarters)을 6분기에서 4분기로 완화.  공통 설계 원칙에 단계별 지표 누적 의존성(상태 보존) 항목 추가  |
| 2026-08-03 | Stage 3, 4의 필터링 로직을 이진 조건(Hard Cut)에서 **Z-score 가중 합산(Composite Score)** 및 상대 퍼센타일 평가로 전면 개편.  매출 역성장, 밸류트랩 등은 탈락 사유가 아닌 경고 태그(Tag)로 전환 |
| 2026-08-04 | Stage 5 재무 건전성 평가를 절대 컷오프에서 Z-score 가중 합산(Composite Score) 방식으로 개편. 턴어라운드 성장주 구제를 위해 ICR에 0.7 가중치 부여 및 경고 태그(Warning Tag) 도입. 생존자 편향 오류 수정을 위해 섹터 상대평가에서 Pool 상대평가로 전환 |
| 2026-08-07 | 공통 설계 원칙 고도화: CQS(명령-조회 분리) 아키텍처 원칙 추가, KOSPI 시세 기반 실제 영업일 추출 및 공시 시차 원천 차단 로직 반영, 자본 증식 가속화를 위한 성장주 프리미엄 원칙 추가 |
| 2026-09-01 | 문서-구현 정합화: "탈락 종목 보존"(fail_reason 컬럼) 패턴을 Stage 1/2/5까지 전체 확장, 그에 맞춰 각 단계 스키마 매핑 갱신(`fail_reason` 필드 반영). Stage4에 `per` 필드 추가(실제 값 채움, 스코어링 미반영), `psr`은 스키마에서 제거하고 TODO로 이관. 한 번도 실제로 쓰인 적 없던 `StockProfile`/`inject_sector_info` 기반 look-ahead bias 방지 서술을 삭제하고 "현재 미구현" 상태로 정정. `na_reasons` 타입이 stage마다 다르다는 점을 명시(통일은 TODO). `fail_reason` Enum 고도화 및 `visualize.py` 라이브러리 선정 TODO는 이미 완료되어 제거 |
| 2026-09-06 | `data/cache` 실데이터 검증 기반 개선: (1) `interest_expense` 계정 매핑을 정확ID→P&L 세부항목→CF 이자지급 라인→광의 금융비용 4단계 우선순위로 재구성(캐시 300개 표본 검증 결과 기존 방식은 70%가 20%p 이상 왜곡). (2) ROIC 분모를 자기자본(사실상 ROE와 동일했던 오류)에서 `total_borrowings + total_equity`(진짜 투하자본)로 수정, `total_borrowings` 필드 신규 추가. (3) 공시 시차 검증의 fail-open 분기 2곳을 fail-closed로 전환(캐시 21,768개 전수조사 결과 지금까지 발동 이력은 없었으나 유일한 look-ahead 방지 장치라 안전하게 닫음). (4) 섹터 지표 집계를 단순평균에서 시가총액가중 평균으로 전환. (5) 섹터 라벨이 point-in-time이 아니라는 한계를 코드 docstring과 1단계 절에 명시(과거 시점 업종 분류 데이터 소스 부재로 당장은 한계로 수용, TODO로 추적). (6) `BacktestEngine`에 `min_portfolio_size`(기본 5) 도입 — 통과 종목이 이 수 미만이면 집중 리스크 회피를 위해 그 분기는 거래를 스킵하고 현금(0%) 처리(지수 부진 시 현금 비중을 높이는 것과 같은 논리). 통과 종목은 충분했으나 전량 시세 데이터가 없어 `performance_log`에서 조용히 누락되던 분기도 같은 방식(현금 처리)으로 통일해 시계열에 구멍이 안 생기도록 수정. (7) 백테스트 엔진의 생존편향 수정: `_get_period_return`이 NaN을 반환한 종목을 조용히 평균에서 빼던 것을, `_is_delisted`로 해당 시점 KOSPI 유니버스 존재 여부를 확인해 실제 상장폐지면 전손(-100%)으로 반영하고, 유니버스엔 남아있는데 시세만 없는 경우(데이터 품질 이슈)만 제외하도록 구분. (8) `params.yaml`의 장식용 `global` 설정 정리: `disclosure_lag_check`는 look-ahead 방지의 유일한 안전장치라 끌 수 없게 아예 삭제, `ttm_denominator`의 `avg_4q`(4개 분기 평균 분모)를 실제로 구현하고 `run_backtest.py`/`cache_warmup.py`에서 loader까지 배선(증자·자사주 매입 등으로 분모가 급변할 때의 외란 완화용). `profitability_basis`의 `"annual"` 분기는 여전히 미구현 상태임을 주석에 명시하고 TODO로 이관. (9) `params.yaml` 전수점검: Stage2 `effective_tax_rate`(0.22, "임시 법인세율"이라 자체 주석에 적혀있던 값), Stage3/4/5의 `calc_zscore` 극단값 클리핑 분위(`zscore_clip_lower/upper`, 기존 0.01/0.99 하드코딩), Stage5의 ICR 상한 캡·클리핑 범위·경고 태그 임계치(`icr_cap`, `icr_clip_lower`, `debt_ratio_clip_upper`, `icr_warning_threshold`, `debt_ratio_warning_percentile`, `min_sample_for_relative_eval`), `BacktestEngine`의 `fee_rate`/`slippage`/`min_portfolio_size`를 전부 params.yaml로 이관(기본값은 기존 하드코딩 값과 동일하게 유지해 회귀 없음). `core/metrics_utils.calc_zscore`는 클리핑 분위를 인자로 받도록 시그니처 변경. 반면 `stage3_fundamental_improve.py`의 `len(q_series) < 6`은 재검토 결과 파라미터화 대상이 아님으로 판단(재고 — `sga_lookback_quarters`와는 별개로 YoY 계산 로직이 index 0/1/4/5를 직접 참조하는 구조적 최소 요구치라, `sga_lookback_quarters`를 바꿔도 이 상수는 6으로 고정이어야 함). (10) params.yaml 그룹 A 마무리: 백테스트 시작/종료 연도가 `run_backtest.py`(argparse 기본값), `cache_warmup.py`의 `get_quarterly_rebalance_dates(2019, 2025)`, `years = list(range(2018, 2026))` 세 곳에 각각 하드코딩돼있던 것을 `backtest.start_year`/`end_year`(신규) 단일 출처로 통합. `run_backtest.py`는 CLI `--start`/`--end`를 우선하되 미지정 시 params.yaml로 폴백하도록 유지, `cache_warmup.py`의 `years` 범위는 `start_year - 1`(TTM이 전년도 분기까지 참조하는 구조)로 파생 계산하도록 변경. (11) DART 캐시 유효기간 분리: 실제 백테스트 실행을 앞두고 캐시 상태를 점검하다가, 공용 `cache_days`(30일)를 그대로 쓰던 DART 재무제표 캐시(`dart_*.csv`, 21,768개)의 95.9%가 만료 판정되어 실행 시 DART 일일 호출 한도(9,500건) 초과로 중단될 위험을 발견. DART 확정 공시 재무제표는 정정공시 등 예외를 빼면 사실상 불변이므로, `dart_cache_days`(기본 3650일)를 신설해 `get_financial_statements`에만 적용하고 나머지 3종 캐시(유니버스/시세/펀더멘털)는 기존 30일 정책 유지 |
| 2026-09-06 | 실제 백테스트 최초 실행 중 발견된 버그 수정: (1) `stage3_fundamental_improve.py`의 `na_reasons`가 list로 초기화됐는데 dict처럼 키 대입되던 타입 버그 수정(신규상장·GPM 계산 불가 종목에서만 발현, 합성 테스트로는 미발견). (2) pykrx 네트워크 순단 대응 — `endpoints.json`의 미사용 `KRX.max_retries`를 실제 배선한 `_fetch_with_retry` 헬퍼를 유니버스/섹터지표/펀더멘털 호출부에 적용, `stage4_valuation.py`는 펀더멘털 조달 실패 시 크래시 대신 결측(구제) 처리, `BacktestEngine.run()`은 분기별 루프를 try/except로 감싸 한 분기의 예외가 전체 다년치 백테스트를 중단시키지 않고 그 분기만 현금 처리 후 계속 진행하도록 개선 |
| 2026-09-08 | 최초 완주한 백테스트(2019~2025, 27분기) 결과 검토 — 누적수익률 전략 50.4% vs KOSPI 94.7%로 저조하나 MDD는 전략이 더 낮음(-25.2% vs -32.3%). 원인 진단 결과 Stage1의 "소외 섹터" 필터가 섹터 단위로 최근 수익률이 낮은 곳만 통과시켜, 코리안 디스카운트 해소 랠리(전력·반도체·방산, 2024-12~2025-09 KOSPI 중앙값 대비 5~25배 상승)를 원천 배제함을 실제 파이프라인 실행(`history` dict 추적)으로 확인. 두 갈래 원인 확인: ① 방산(079550)은 Stage1 자체에서 `sector_not_qualified`로 배제(섹터 수익률 z=-2.9, 개별 기업 펀더멘털 평가 기회 자체가 없음). ② 전기장비(019180)는 Stage1은 통과했으나 Stage3에서 `composite_score_below_cutoff`로 탈락(매출성장 +19%는 양호했으나 판관비 증가율 +28.6%가 매출성장보다 빨라 마진 하락). "하드컷 → penalty 항" 전환(B안, 전 요인 통합 스코어링)과 "계산비용 게이트/모멘텀 신호 분리"(C안) vs "국소 조정"(A안)을 비교한 결과, B안은 Stage1이 겸하고 있는 DART 호출량 절감(퍼널이 좁아질수록 이후 단계 계산량도 줄어듦) 효과를 없애 일일 한도 문제를 재발시킬 위험이 커 보류. 우선 A안 적용: (1) Stage1 `pass_ratio` 0.4→0.5로 완화(1차 검증용). (2) Stage3에 현금흐름 구제 신호 신설 — 매출성장 있고 TTM OCF가 건실(`OCF>0` 이고 `OCF≥순이익`)하면 판관비/GPM 악화로 인한 컷오프 미달이어도 구제(`CASH_FLOW_QUALITY_RESCUE`), 매출 역성장은 현금흐름이 좋아도 구제 대상 아님 — 확정 수주 기반 선투자(방산 등)와 순수 테마성 지출을 매출의 현금 전환 여부로 구분하려는 의도. 동일한 재무수치에 현금흐름만 다르게 준 합성 테스트로 구제 대상만 정확히 걸러짐을 검증 |
| 2026-09-09 | A안 적용 후 재실행한 백테스트가 오히려 전 지표에서 악화(누적수익률 50.4%→29.0%, MDD -25.2%→-31.5%)되어 Stage1 완화(Config A)와 Stage3 현금흐름 구제(Config B)를 각각 단독 실행하는 A/B 격리 테스트 진행. Config A는 단독으로 baseline보다도 개선(누적 54.3%, MDD -25.1%)된 반면, pass_ratio를 고정한 "Both ON vs Config A" 비교에서 구제 활성화 쪽이 확연히 나빠(누적 29.0% vs 54.3%) 구제 로직이 원인으로 확정. 조사 과정에서 `core/pipeline.py`의 `_accumulate_results()`가 `fail_reason`만 특별 처리하고 있어 Stage2/3/5가 공유하는 `na_reasons`(및 Stage3/5가 공유하는 `ocf`/`net_income`) 컬럼명이 겹치면 이후 단계가 새로 계산한 값이 병합 시 조용히 버려지고 이전 단계의 오래된 값이 남는 버그를 발견·수정(겹치는 컬럼은 항상 최신 단계 결과가 우선하도록 일반화) — 통과/탈락 판정 자체엔 영향 없었지만 `history` dict를 통한 사후 감사가 Stage3부터 무력화돼 있었음. 수정 후 재확인한 실측 결과, 구제 조건("매출성장 0%↑ + OCF≥순이익")이 Stage3 후보의 32%(2021-09-30 샘플)를 무차별 통과시키고 있었음을 확정 — `cash_flow_rescue_enabled: false`로 비활성화 확정, `stage1_pass_ratio: 0.5`는 단독 검증된 개선이라 유지. 부수적으로 Stage3/4의 컴포짓 스코어가 처리되지 않은 결측 경로로 NaN이 될 때 `na_reasons` 태그 없이 자동 통과되는 소규모(3%/2%) 경로도 발견해 TODO로 이관 |
| 2026-09-10 | 전날 발견한 TODO 2건 해소. (1) Stage3/4 NaN 자동통과 태깅: 컴포짓 스코어의 하위 요소가 (기존 exempt 조건 밖에서) 결측이면 `NaN < cutoff` 비교가 항상 False가 되어 무태그로 통과하던 경로에 `SCORE_NOT_COMPUTABLE` 태그를 명시 추가(Stage4는 기존에 없던 `na_reasons` 컬럼 자체를 신설하며 기존 PBR 결측 구제 경로에도 `PBR_NOT_COMPUTABLE` 태그를 함께 부여). 실제 파이프라인 재실행으로 태그 개수(Stage3 4건, Stage4 1건, 전날 진단과 일치)와 최종 통과 종목 수·구성이 기존과 동일함을 확인 — 필터링 결과에 영향 없이 감사 가능성만 개선. (2) 섹터-종목 기준일 역전(look-ahead bias) 재검토: `pipeline.run(base_date)`이 `get_kospi_universe`/`get_sector_metrics`를 동일한 `base_date`로 호출하고 둘 다 같은 순수함수(`_get_nearest_past_bday`, 숨은 전역 상태 없음)로 영업일을 해석함을 코드 추적으로 확인, 섹터 데이터 원천(pykrx 시세)은 DART와 달리 공시 시차 개념이 아예 적용되지 않는 실시간 확정 데이터라는 점까지 더해 별도 체크가 방어할 실제 시나리오가 현재 구조엔 없다고 결론 — `inject_sector_info`가 막던 문제는 이미 삭제된 구 아키텍처(`StockProfile`)에 국한됐던 것으로 판단, 코드 추가 없이 TODO 종료. (3) `na_reasons` 타입 통일: Stage3만 `dict[str, tuple[MetricStatus, str]]`이던 것을 Stage2/4/5와 같은 comma-joined `str`로 변경 — 값이 다시 읽힌 적이 없어(항상 태그명 포함 여부만 확인) 손실 없음을 확인 후 진행, 재실행으로 태그 개수·최종 통과 종목 동일함을 검증. (4) Stage3 현금흐름 구제 재설계: 매출성장 임계치 0%→15% 상향, 적자 기업(`net_income<=0`) 배제(구 버전은 이 경우 `OCF≥순이익`이 사실상 항상 참이 되어 이익의 질 검증이 무력화됨) 후 전체 백테스트 재검증 — 무차별 구제 문제(2021-09-30 샘플 구제 대상이 실제로 15%↑·흑자 기업으로 좁혀짐을 확인)는 해소됐으나, 성과가 구제 완전 비활성화 대비 여전히 낮아(누적 47.3% vs 54.3%, Sharpe 3.18 vs 3.35, MDD만 -24.7%로 근소 우위) 최종 비활성화 유지로 결론. 재설계된 코드/기준값은 향후 재시도를 위해 보존 |
| 2026-09-10 (2) | `fail_reason`/`na_reasons`/`warning_tags` 전체 레퍼런스를 새 "부록" 절로 추가 — 5개 stage에 흩어져 있던 태그를 코드 전수 grep으로 검증해 표 3개(fail_reason 5종, na_reasons 9종, warning_tags/boolean 경고 6종)로 정리. Stage3/4 절의 개별 "업종 편차 주의" 목록에 누락돼 있던 `SCORE_NOT_COMPUTABLE`(둘 다), `PBR_NOT_COMPUTABLE`(Stage4, 태그명 명시 안 돼 있던 것) 보강. `CRITICAL_METRIC_NOT_COMPUTABLE`이 스키마에 정의만 되고 실제로 쓰인 적 없는 죽은 값임을 확인해 명시 |
| 2026-09-12 | `global.profitability_basis: "annual"` vs `"ttm"` A/B 백테스트 완료 — 27개 분기 전체 실행 결과 annual이 누적수익률(-1.9% vs 54.3%)·MDD(-39.2% vs -25.1%)·Sharpe(0.01 vs 0.32)·승률(33.3% vs 37.0%) 전부에서 열세로 확인. 확정 사업보고서의 공시 지연(최대 ~21개월)으로 인한 데이터 시의성 손실이 원인으로 추정(annual 모드 평균 통과 종목 수 14.9개 vs ttm 12.0개, 더 느슨한 게이트). 기본값 `"ttm"` 최종 확정, `"annual"` 코드는 보존만 |
| 2026-09-11 (6) | DART/KRX 호출부 무한 행(hang) 버그 수정 완료 — `requests.get()`에 timeout 미지정이 원인. `socket.setdefaulttimeout()`은 requests가 무시함을 실측 확인 후, `requests.Session.request` 몽키패치로 해결(`_patch_requests_default_timeout`). 로컬 hang 서버 재현 테스트 + smoke_test_loader.py 전체 통과로 검증 |
| 2026-09-11 (5) | `annual` 백테스트 A/B 검증 첫 시도 중 위 버그 발견(7시간+ 무응답), 사용자 요청으로 백테스트는 일시 보류 |
| 2026-09-11 (4) | `global.profitability_basis: "annual"` 경로 구현 — Stage2 ROE/ROIC에 한해 최근 N개년(기본 3) 확정 사업보고서의 "수준+추세" 가중블렌딩으로 계산하는 방식 추가(기본값은 여전히 "ttm", 백테스트 검증 전). 사용자와 설계 논의 끝에 Stage2(연간, 장기 기반 확인)·Stage3(분기, 단기 가속 포착) 역할 분담 구조로 확정, Stage2의 기존 "3개 지표 독립 percentile 게이트" 원칙은 그대로 유지(값 계산 방식만 교체) |
| 2026-09-11 (3) | `psr`(시가총액/TTM매출) 계산 로직 구현 — `ValuationMetrics.psr` 필드 신설, Stage4에서 `loader.get_ttm_financials()`로 DART TTM 매출을 조회해 계산. `per`과 동일하게 값만 보존, composite score 미반영. 2025-09-30 기준 실제 검증 결과 Stage4 56개 종목 전원 계산 성공, 최종 통과 수·구성 기존과 동일, 신규 DART API 호출 0건(Stage3가 이미 예열해둔 캐시 재사용) |
| 2026-09-11 (2) | 재무 데이터 발표 시점 지연(lag) 추적 필드 신설 — `QuantDataLoader.disclosure_lag_log`(리스트, 인스턴스 속성). `parse_standardized_financials`가 공시일(`rcept_dt`)을 계산할 때마다 `base_date` 대비 지연 일수를 기록만 하는 감사용 메타데이터로, 판정 로직(`standard_metrics`)과는 완전히 분리된 채널이라 필터링 결과엔 영향 없음. TODO 문구에 있던 `sector_data_lag_days`가 실제로는 코드에 존재한 적 없던 오기였음도 함께 확인·정정 |
| 2026-09-11 | 1단계 "섹터 라벨 point-in-time" TODO 조사 완료 — KRX 공식 업종분류현황(`get_market_sector_classifications`, date 인자로 실제 point-in-time 확인)과 KRX 테마지수 구성종목(`get_index_portfolio_deposit_file`) 두 후보를 라이브 호출로 검증했으나, 전자는 분류가 24~26개로 너무 거칠고(현재 ~126개 세분류 대비 방산 등 니치 섹터가 뭉개짐) 후자는 curated TOP-N이라 전체 커버리지가 안 돼 둘 다 기각, 한계 유지로 결론. 코드 변경 없음. 조사 도중 별개로 발견한 pykrx 인증 세션 버그(아래 decisions_log.md 2026-09-11 항목 참고)는 `data/loader.py`, `backtest/run_backtest.py`에서 수정 완료 |
| 2026-09-10 (3) | `main.py`(실전 스크리닝 진입점) 착수. `pipeline.run(base_date)`을 1회 실행해 최종 통과 종목 + 단계별 이력(history)을 `outputs/`에 CSV로 저장만 하는 Command 계층으로 구현하고, 요약·시각화는 신설한 `analysis/screening_stats.py`(순수 함수: 깔때기 요약 `build_funnel_summary`, 티커별 탈락사유 리포트 `build_rejection_report`)·`analysis/visualize_screening.py`(정적 PNG/동적 HTML 렌더링)에 위임 — `backtest/run_backtest.py`+`analysis/visualize.py`와 동일한 CQS 패턴. 예전 세 개의 디버깅용 주피터 노트북(`01_data_loader_test`, `02_strategy_pipeline`, `03_debugging_and_backtest`)은 삭제하고, 실제 프로덕션 코드를 그대로 호출하는 `debugging/` 폴더(스크린 스모크 테스트, 단일 stage 격리 실행, 티커 추적, DART 원본 계정 덤프)로 대체 — 옛 노트북이 stage 파라미터를 셀에 직접 하드코딩해두거나(계속 낡아버림) Stage5 로직을 통째로 복제해둔 채 최신 코드와 어긋나 있던(2026-09-10 확인) 문제를 근본적으로 없앴다. 2025-09-30 기준 종단 검증 완료(847→339→150→56→18→14) |