import sys
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path

# 프로젝트 루트 경로 설정 (imports 에러 방지, backtest/run_backtest.py와 동일한 규칙)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from analysis.stats import calc_cumulative_returns, calc_mdd

class BacktestVisualizer:
    def __init__(self, perf_csv_path: Path, port_csv_path: Path, out_dir: Path):
        self.perf_df = pd.read_csv(perf_csv_path, parse_dates=['date'])
        self.port_df = pd.read_csv(port_csv_path, parse_dates=['date'])
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # CQS 원칙: 통계 연산은 stats.py(순수 함수)에 위임, 여기서는 렌더링만 담당
        self.perf_df['cumulative_return'] = calc_cumulative_returns(self.perf_df['portfolio_return'])
        _, self.perf_df['drawdown'] = calc_mdd(self.perf_df['portfolio_return'])

        # Matplotlib 한글 폰트 깨짐 방지 세팅 (맥북 환경 고려)
        plt.rcParams['font.family'] = 'AppleGothic'
        plt.rcParams['axes.unicode_minus'] = False

    def generate_static_report(self):
        """
        [MacBook용] Matplotlib & Seaborn을 활용한 가벼운 정적 이미지 리포트
        - 장점: 렌더링이 매우 빠르고 리소스를 거의 차지하지 않음
        - 출력: outputs/static_report.png
        """
        print("📊 [1/2] 정적 리포트(PNG) 생성 중...")
        
        fig, axes = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
        
        # 1. 누적 수익률 차트
        axes[0].plot(self.perf_df['date'], self.perf_df['cumulative_return'], color='royalblue', linewidth=2)
        axes[0].set_title('Portfolio Cumulative Return', fontsize=16, fontweight='bold')
        axes[0].set_ylabel('Return')
        axes[0].grid(True, linestyle='--', alpha=0.6)
        
        # 2. MDD (낙폭) 수중 차트
        axes[1].plot(self.perf_df['date'], self.perf_df['drawdown'], color='crimson', alpha=0.7)
        axes[1].fill_between(self.perf_df['date'], self.perf_df['drawdown'], 0, color='crimson', alpha=0.3)
        axes[1].set_title('Drawdown (MDD)', fontsize=14)
        axes[1].set_ylabel('Drawdown (%)')
        axes[1].grid(True, linestyle='--', alpha=0.6)
        
        plt.tight_layout()
        save_path = self.out_dir / "static_report.png"
        fig.savefig(save_path, dpi=150)
        plt.close(fig)

    def generate_interactive_report(self):
        """
        [Desktop용] Plotly를 활용한 동적 HTML 리포트
        - 장점: 마우스 오버 툴팁, 드래그 줌인, 범례 클릭 온오프 등 심층 분석 가능
        - 출력: outputs/interactive_report.html
        """
        print("📈 [2/2] 동적 리포트(HTML) 생성 중...")
        
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                            vertical_spacing=0.1, 
                            subplot_titles=('Cumulative Return', 'Drawdown (MDD)'),
                            row_heights=[0.7, 0.3])
        
        # 1. 누적 수익률 
        fig.add_trace(go.Scatter(x=self.perf_df['date'], y=self.perf_df['cumulative_return'],
                                 mode='lines', name='Return', line=dict(color='royalblue', width=2)),
                      row=1, col=1)
        
        # 2. MDD
        fig.add_trace(go.Scatter(x=self.perf_df['date'], y=self.perf_df['drawdown'],
                                 mode='lines', name='MDD', fill='tozeroy',
                                 line=dict(color='crimson'), fillcolor='rgba(220, 20, 60, 0.3)'),
                      row=2, col=1)
        
        fig.update_layout(title_text="퀀트 백테스트 심층 분석 리포트", height=800, hovermode="x unified")
        
        save_path = self.out_dir / "interactive_report.html"
        fig.write_html(str(save_path))

    def run_all(self):
        self.generate_static_report()
        self.generate_interactive_report()
        print("✅ 모든 시각화 리포트 산출 완료!")

# 단독 테스트용 실행 블록
if __name__ == "__main__":
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    out_dir = PROJECT_ROOT / "outputs"

    # run_backtest.py가 실행 시각(YYYYMMDD_HHMMSS)을 파일명에 포함시켜 저장하므로(과거엔 고정
    # 파일명이라 재실행할 때마다 이전 결과가 조용히 덮어써졌음), 가장 최근 실행분을 자동으로 찾는다.
    # 타임스탬프 포맷상 파일명 정렬 순서가 곧 시간 순서와 같다.
    perf_candidates = sorted(out_dir.glob("performance_log_*.csv"))
    port_candidates = sorted(out_dir.glob("portfolio_log_*.csv"))

    if perf_candidates and port_candidates:
        perf_csv = perf_candidates[-1]
        port_csv = port_candidates[-1]
        print(f"📄 가장 최근 백테스트 결과 사용: {perf_csv.name}")
        viz = BacktestVisualizer(perf_csv, port_csv, out_dir)
        viz.run_all()
    else:
        print("⚠️ 아직 백테스트 결과 파일이 존재하지 않습니다. run_backtest.py를 먼저 실행하세요.")