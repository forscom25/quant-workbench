"""
특정 종목이 파이프라인의 어느 stage에서, 왜 탈락(혹은 최종 통과)했는지 추적하는 도구.

예전 03_debugging_and_backtest.ipynb의 여러 하드코딩된 "이 티커 왜 떨어졌지" 셀들
(Stage3 탈락 원인, invalid 티커 딥다이브, exempt 구제 확인 등)을 이 하나로 통합했다.
탈락 사유 판정 로직은 analysis/screening_stats.build_rejection_report()를 그대로
재사용한다 — 같은 로직을 여기서 다시 구현하면 두 군데가 따로 낡는 문제가 재발하므로.

사용 예:
    # 오늘 기준으로 파이프라인을 새로 돌려서 추적 (DART 신규 호출 발생 가능)
    python3 debugging/trace_ticker.py --ticker 103140 --date 2025-09-30

    # main.py가 가장 최근에 저장해둔 결과를 재사용 (빠름, API 호출 없음)
    python3 debugging/trace_ticker.py --ticker 103140 --from-latest
"""
import sys
import argparse
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import yaml

from data.loader import QuantDataLoader
from core.pipeline import QuantPipeline
from analysis.screening_stats import STAGE_ORDER, build_rejection_report
from analysis.visualize_screening import _load_latest_history

def main():
    parser = argparse.ArgumentParser(description="티커별 탈락 stage/사유 추적 도구")
    parser.add_argument("--ticker", type=str, required=True, help="추적할 티커 (예: 005930)")
    parser.add_argument("--date", type=str, default=None, help="기준일 YYYY-MM-DD (기본: 오늘, --from-latest와 함께 쓸 수 없음)")
    parser.add_argument("--from-latest", action="store_true", help="main.py가 저장한 가장 최근 결과를 재사용(API 호출 없음)")
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / "outputs"

    if args.from_latest:
        history = _load_latest_history(out_dir)
        if history is None:
            print("❌ outputs/에 main.py 실행 결과가 없습니다. 먼저 main.py를 실행하거나 --date를 지정하세요.")
            return
    else:
        base_date = date.fromisoformat(args.date) if args.date else date.today()
        with open(PROJECT_ROOT / "config" / "params.yaml", "r", encoding="utf-8") as f:
            params = yaml.safe_load(f)
        ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
        loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)
        pipeline = QuantPipeline(params, loader)
        print(f"🚀 파이프라인 실행 중 (기준일 {base_date})...\n")
        _, history = pipeline.run(base_date)

    report = build_rejection_report(history)
    row = report[report['ticker'] == args.ticker]

    print("==================================================")
    print(f"🔍 [{args.ticker}] 추적 결과")
    print("==================================================")

    if row.empty:
        print("이 종목은 애초에 유니버스(Stage1 입력)에 존재하지 않습니다 — 상장폐지, 코스피 미편입, 티커 오타 등을 의심하세요.")
        return

    r = row.iloc[0]
    if r['status'] == 'PASSED':
        print(f"✅ 최종 통과 ({r['last_stage']}까지 전부 통과)")
    else:
        print(f"🚨 {r['last_stage']}에서 탈락")
        print(f"   fail_reason : {r['fail_reason']}")
    print(f"   na_reasons  : {r['na_reasons'] or '(없음)'}")

    # 도달한 마지막 stage의 원본 row 전체도 함께 보여준다 (구체 수치 확인용)
    last_df = history.get(r['last_stage'])
    if last_df is not None:
        detail = last_df[last_df['ticker'] == args.ticker]
        if not detail.empty:
            print(f"\n📊 [{r['last_stage']}] 원본 데이터")
            print(detail.to_string(index=False))

if __name__ == "__main__":
    main()
