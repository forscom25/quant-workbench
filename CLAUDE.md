# quant_project

국내 주식 5단계 필터 스크리너(소외 섹터 → 섹터 리더 → 펀더멘털 개선 → 밸류에이션 → 재무 건전성) + 백테스트. 코드 루트는 `stock_screener/`.

## 환경 / 명령어
- Windows. Python은 `quant_env\Scripts\python.exe` (`python3`, `caffeinate` 아님). 가상환경 폴더는 열지 말 것.
- 진입점: `stock_screener/main.py`(실전), `backtest/run_backtest.py --start --end`, `backtest/cache_warmup.py`, `backtest/*_signal_analysis.py`, `backtest/warning_tag_gate_analysis.py`
- 후보 점검: `main.py --date YYYY-MM-DD` 실행 후 `scripts/candidate_report.py`(API 없음, `--min-cap` 기본 1000억, `--exclude-loss`, `--min-icr`). 보유 종목은 `private/holdings.yaml`(git 제외, 개인 자산 파일은 `private/`에만 둘 것)
- 검증: `backtest/stage_pass_analysis.py`(KRX 순차) → `analysis/stage_pass_stats.py`(조회 전용)
- 출력이 긴 스크립트 로그(DART 라이브러리 출력 수천 줄)는 통째로 Read 하지 말고 Grep으로 요약 줄만 확인
- `debugging/`: 소수 종목 재현용(`run_single_stage.py`, `trace_ticker.py`, `inspect_raw_dart.py`, `smoke_test_loader.py`)

## 아키텍처 규칙 (상세: docs/architecture.md)
- 판단 기준: "params.yaml 값이 바뀌면 결과가 달라지는가?" Yes → stage, No(데이터 정합성) → loader. pipeline.py는 계산 0줄(오케스트레이션만).
- 탈락 종목 row 삭제 금지. `fail_reason`(core/schema.py `FailReason`)만 채우고 `history` dict에 보존.
- CQS: `backtest/`=실행·파일 쓰기, `analysis/`=읽기 전용(loader/pipeline import 금지).
- 임계치·가중치 하드코딩 금지 → `config/params.yaml`.
- 결측: 업종 특성상 계산 불가 → 구제(exempt), 데이터 부족 → 기계적 탈락.
- 공시 시차 검증은 fail-closed. 끄는 옵션을 만들지 않는다.
- 이진 컷오프 대신 Z-score 합산 + 상위 N% (Stage 2만 지표별 percentile AND).

## 확정된 설정 (A/B 백테스트로 검증됨, 근거 없이 되돌리지 말 것)
`profitability_basis: ttm`, `ttm_denominator: latest_snapshot`, `stage4 per_weight: 0.0`, `cash_flow_rescue_enabled: false`, `stage1_pass_ratio: 0.5`.
annual / avg_4q / per 추가는 전부 성과 악화. 새 팩터·정교화는 기본적으로 의심하고, 저비용 IC 분석을 먼저 한 뒤에만 27분기 전체 백테스트를 돌린다.

## 사고 방지 (반드시 준수)
- KRX 호출 스크립트는 내가 실행하지 않고 `! <명령>` 실행용 명령만 안내한다(사용자가 직접 실행, 분석 스크립트만 내가 실행). 로그인 실패·`JSONDecodeError`가 한 번이라도 나면 그날은 KRX 재시도 금지.
- KRX를 호출하는 스크립트는 **순차 실행만**. 병렬·동시 백그라운드 실행 금지(IP 1일 차단 2회 발생). 결과의 `건너뜀` 개수와 표본 분기 수를 확인해 보고.
- DART 키는 맥과 공유, 일일 한도 19,000건. `cache_warmup.py` 등 대량 호출은 실행 전 사용자 확인.
- `stock_screener/data/cache`는 수정·삭제 금지. `.empty` 마커는 한도 초과로 생긴 가짜 "없음"일 수 있음.
- 읽기 차단 경로(`quant_env`, `data/cache`, `outputs`, `docs_cache`, `.env`, `*.pkl`)를 Bash/PowerShell로 우회해 읽지 말 것.

## 남은 작업
- [x] 파이프라인 효과 직접 검증 완료(2026-10-04): 사전 기준 미충족, 알파 미입증(최종 통과 vs 무작위 백분위 0.56, t=0.44). 단계별 기여 없음, 5종목 순위 규칙도 무작위와 구분 불가
- [ ] (상장폐지 처리 확인 완료 2026-10-04: -100% 반영됨) 단순 팩터(저PBR·ROE·모멘텀·저변동성) 기준선과 비교, 파라미터 동결 페이퍼 트레이딩 기록
- [ ] Stage5 부채비율 업종 상대기준 산출 방식 확정
- [ ] Stage3/4 컷오프 30% → 20% 검토
- [ ] (우선순위 하향) `[이익질주의]` 게이트 A/B, Stage1 penalty 전환
- 현재 결론: 알파 미입증(위 검증). 이 도구는 후보 깔때기이며 rank_score를 선택 근거로 쓰지 말 것. 사용자는 5종목 안팎 집중 포트의 후보 발굴용으로 사용하고 최종 선택은 직접 한다.

## 문서 읽기 (토큰 절약)
- `docs/architecture.md`(113줄)만 통째로 읽어도 됨. `docs/screening_criteria.md`(~300줄), `docs/decisions_log.md`(~570줄)는 **전체 Read 금지** — Grep으로 키워드(`## 2026-09-`, `TODO`, stage 이름)를 찾고 offset/limit로 해당 구간만.
- `@docs/...` import 금지.
- 갱신 규칙: 확정 설계 → architecture.md / screening_criteria.md, 논의·이력 → decisions_log.md 맨 아래에 날짜 항목 추가(`[결정]` `[진행 중]` `[보류]`).

## 작업 습관
- 탐색은 Grep/Glob을 `stock_screener/` 하위로 한정. 큰 CSV는 head/pandas 요약만.
- 주석은 why 위주, 기존 한국어 docstring 톤 유지.
- 새 테스트는 결측·예외 분기를 일부러 트리거(정상 경로만 테스트해 stage3 버그를 놓친 전례).
- 작업이 끝나면 `/clear`, 긴 세션은 `/compact`.
