import sys
import pandas as pd
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from pathlib import Path
from typing import Dict, Optional

# 프로젝트 루트 경로 설정 (imports 에러 방지, backtest/run_backtest.py와 동일한 규칙)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from analysis.screening_stats import STAGE_ORDER, build_funnel_summary, build_rejection_report

class ScreeningVisualizer:
    def __init__(self, history: Dict[str, pd.DataFrame], out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # CQS 원칙: 통계/집계 연산은 screening_stats.py(순수 함수)에 위임, 여기서는 렌더링만 담당
        self.funnel_df = build_funnel_summary(history)
        self.rejection_df = build_rejection_report(history)

        # Matplotlib 한글 폰트 깨짐 방지 세팅 (맥북 환경 고려, visualize.py와 동일)
        plt.rcParams['font.family'] = 'AppleGothic'
        plt.rcParams['axes.unicode_minus'] = False

    def generate_static_report(self):
        """
        [MacBook용] 단계별 통과 인원 깔때기를 막대그래프로 표시하는 정적 이미지 리포트
        - 출력: outputs/static_screening_report.png
        """
        print("📊 [1/2] 정적 스크리닝 리포트(PNG) 생성 중...")

        fig, ax = plt.subplots(figsize=(10, 6))
        stages = self.funnel_df['stage']
        totals = self.funnel_df['total']
        passed = self.funnel_df['passed']

        ax.bar(stages, totals, color='lightgray', label='평가 대상(total)')
        ax.bar(stages, passed, color='royalblue', label='통과(passed)')

        for i, (t, p) in enumerate(zip(totals, passed)):
            ax.text(i, t, str(t), ha='center', va='bottom', fontsize=9, color='gray')
            ax.text(i, p, str(p), ha='center', va='bottom', fontsize=9, color='royalblue', fontweight='bold')

        ax.set_title('스크리닝 깔때기 (단계별 통과 인원)', fontsize=16, fontweight='bold')
        ax.set_ylabel('종목 수')
        ax.legend()
        ax.grid(True, axis='y', linestyle='--', alpha=0.6)

        plt.tight_layout()
        save_path = self.out_dir / "static_screening_report.png"
        fig.savefig(save_path, dpi=150)
        plt.close(fig)

    def generate_interactive_report(self):
        """
        [Desktop용] Plotly 퍼널 차트를 활용한 동적 HTML 리포트
        - 출력: outputs/interactive_screening_report.html
        """
        print("📈 [2/2] 동적 스크리닝 리포트(HTML) 생성 중...")

        fig = go.Figure(go.Funnel(
            y=self.funnel_df['stage'],
            x=self.funnel_df['passed'],
            textinfo="value+percent initial"
        ))
        fig.update_layout(title_text="스크리닝 깔때기 (단계별 통과 인원)", height=600)

        save_path = self.out_dir / "interactive_screening_report.html"
        fig.write_html(str(save_path))

    def save_reports(self):
        """funnel 요약과 티커별 탈락사유 리포트를 CSV로도 저장 (재사용/필터링 편의)"""
        self.funnel_df.to_csv(self.out_dir / "screening_funnel_summary.csv", index=False)
        self.rejection_df.to_csv(self.out_dir / "screening_rejection_report.csv", index=False, encoding="utf-8-sig")
        print(f"📄 탈락사유 리포트: {len(self.rejection_df)}개 종목 "
              f"(통과 {(self.rejection_df['status'] == 'PASSED').sum()}개 / "
              f"탈락 {(self.rejection_df['status'] == 'REJECTED').sum()}개)")

    def run_all(self):
        self.generate_static_report()
        self.generate_interactive_report()
        self.save_reports()
        print("✅ 모든 스크리닝 리포트 산출 완료!")

def _load_latest_history(out_dir: Path) -> Optional[Dict[str, pd.DataFrame]]:
    """
    main.py가 남긴 outputs/screening_final_{timestamp}.csv를 기준으로 가장 최근 실행의
    timestamp를 확정한 뒤, 같은 timestamp의 stage1~5 CSV를 모아 history dict로 재구성한다.
    (stageN 파일 각각을 따로 glob하면, 실행마다 도달한 마지막 단계가 다를 수 있어(예: 어느 분기는
    stage3에서 전멸) 서로 다른 실행의 파일이 섞일 위험이 있다 — final 파일 하나로 실행을 고정.)
    """
    final_candidates = sorted(out_dir.glob("screening_final_*.csv"))
    if not final_candidates:
        return None

    latest_final = final_candidates[-1]
    timestamp = latest_final.stem.replace("screening_final_", "")

    history = {}
    for stage_name in STAGE_ORDER:
        stage_path = out_dir / f"screening_{stage_name}_{timestamp}.csv"
        if stage_path.exists():
            df = pd.read_csv(stage_path, dtype={'ticker': str})
            if 'na_reasons' in df.columns:
                df['na_reasons'] = df['na_reasons'].fillna('')
            history[stage_name] = df

    print(f"📄 가장 최근 스크리닝 결과 사용: timestamp={timestamp}")
    return history

# 단독 테스트용 실행 블록
if __name__ == "__main__":
    out_dir = PROJECT_ROOT / "outputs"
    history = _load_latest_history(out_dir)

    if history:
        viz = ScreeningVisualizer(history, out_dir)
        viz.run_all()
    else:
        print("⚠️ 아직 스크리닝 결과 파일이 존재하지 않습니다. main.py를 먼저 실행하세요.")
