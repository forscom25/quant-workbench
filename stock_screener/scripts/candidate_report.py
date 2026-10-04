"""
main.py가 가장 최근에 저장한 스크리닝 결과로 "후보 리포트"와 "보유 종목 점검"을 만든다.

API 호출이 전혀 없다(outputs/의 CSV만 읽음). 파이프라인 실행은 main.py가 담당하고, 여기서는
읽기·가공(Query)만 한다 — backtest/analysis 분리(CQS)와 같은 원칙.

사용 예:
    quant_env\\Scripts\\python.exe scripts\\candidate_report.py
    quant_env\\Scripts\\python.exe scripts\\candidate_report.py --top-n 5 --max-per-sector 1
"""
import sys
import argparse
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import pandas as pd
import yaml

from analysis.candidate_report import build_candidate_report, build_holdings_check
from analysis.screening_stats import build_funnel_summary
from analysis.visualize_screening import _load_latest_history


def main():
    parser = argparse.ArgumentParser(description="후보 리포트 + 보유 종목 점검 (API 호출 없음)")
    parser.add_argument("--top-n", type=int, default=5, help="shortlist 최대 종목 수")
    parser.add_argument("--max-per-sector", type=int, default=1, help="shortlist에서 섹터당 최대 종목 수")
    parser.add_argument("--min-cap", type=float, default=1000, help="shortlist 시총 하한(억 원, 기본 1000, 0이면 해제)")
    parser.add_argument("--exclude-loss", action="store_true", help="ROE<0 종목을 shortlist에서 제외")
    parser.add_argument("--min-icr", type=float, default=None, help="이자보상배율이 이 값 미만이면 shortlist에서 제외(예: 1)")
    parser.add_argument("--holdings", type=str, default=str(PROJECT_ROOT / "private" / "holdings.yaml"))
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / "outputs"
    history = _load_latest_history(out_dir)
    if history is None:
        print("❌ outputs/에 main.py 실행 결과가 없습니다. 먼저 main.py를 실행하세요.")
        return

    pd.set_option('display.width', 200)
    pd.set_option('display.max_columns', 30)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\n=== 깔때기 요약 ===")
    print(build_funnel_summary(history).to_string(index=False))

    candidates = build_candidate_report(
        history, max_per_sector=args.max_per_sector, top_n=args.top_n,
        min_market_cap=args.min_cap * 1e8 if args.min_cap > 0 else None,
        exclude_loss=args.exclude_loss, min_icr=args.min_icr,
    )
    print(f"\n=== 후보 리포트 (최종 통과 {len(candidates)}개, shortlist = 점수순·섹터당 {args.max_per_sector}개·최대 {args.top_n}개) ===")
    if candidates.empty:
        print("(최종 통과 종목 없음)")
    else:
        print(candidates.round(3).to_string(index=False))
        candidates.to_csv(out_dir / f"candidates_{timestamp}.csv", index=False, encoding="utf-8-sig")

    holdings_path = Path(args.holdings)
    if holdings_path.exists():
        with open(holdings_path, "r", encoding="utf-8") as f:
            holdings = yaml.safe_load(f).get('holdings', {})
        check = build_holdings_check(history, {str(k): v for k, v in holdings.items()})
        print("\n=== 보유 종목 점검 ===")
        print(check.round(3).to_string(index=False))
        check.to_csv(out_dir / f"holdings_check_{timestamp}.csv", index=False, encoding="utf-8-sig")
    else:
        print(f"\n(보유 종목 파일 없음: {holdings_path})")


if __name__ == "__main__":
    main()
