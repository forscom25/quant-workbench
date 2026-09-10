import sys
import argparse
import yaml
from datetime import date, datetime
from pathlib import Path

# 프로젝트 루트 경로 설정 (imports 에러 방지, backtest/run_backtest.py와 동일한 규칙)
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from data.loader import QuantDataLoader
from core.pipeline import QuantPipeline

def main():
    # 실전 스크리닝 진입점 — 백테스트(backtest/run_backtest.py)와 달리 과거 구간을 순회하지 않고,
    # 지정된(기본: 오늘) 단일 기준일에 대해 파이프라인을 한 번 실행해 "지금 담을 종목"을 산출한다.
    parser = argparse.ArgumentParser(description="Stock Screener 실전 진입점")
    parser.add_argument("--date", type=str, default=None, help="기준일 YYYY-MM-DD (기본: 오늘)")
    args = parser.parse_args()

    base_date = date.fromisoformat(args.date) if args.date else date.today()

    config_path = PROJECT_ROOT / "config" / "params.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            params = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
        return

    print("==================================================")
    print(f"🚀 실전 스크리닝 가동: 기준일 {base_date}")
    print("==================================================\n")

    ttm_denominator = params.get('global', {}).get('ttm_denominator', 'latest_snapshot')
    loader = QuantDataLoader(use_cache=True, ttm_denominator=ttm_denominator)
    pipeline = QuantPipeline(params, loader)

    final_df, history = pipeline.run(base_date)

    # CQS 원칙: 여기서는 파이프라인 실행과 결과 저장(Command)만 담당하고, 깔때기 요약/탈락사유
    # 리포트/차트 같은 조회(Query) 성격의 가공은 analysis/screening_stats.py·visualize_screening.py에
    # 위임한다 — backtest/run_backtest.py와 analysis/visualize.py 관계와 동일한 패턴.
    output_dir = PROJECT_ROOT / "outputs"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    final_path = output_dir / f"screening_final_{timestamp}.csv"
    final_df.to_csv(final_path, index=False, encoding="utf-8-sig")

    for stage_name, stage_df in history.items():
        stage_path = output_dir / f"screening_{stage_name}_{timestamp}.csv"
        stage_df.to_csv(stage_path, index=False, encoding="utf-8-sig")

    print("\n==================================================")
    print(f"✅ 스크리닝 완료! 최종 통과 {len(final_df)}개 종목")
    print(f"   - 최종 결과: {final_path}")
    print(f"   - 단계별 이력: outputs/screening_stage1~{len(history)}_{timestamp}.csv")
    print("==================================================")

if __name__ == "__main__":
    main()
