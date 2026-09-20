"""
신호 분석 스크립트(stage1/stage4_signal_analysis, warning_tag_gate_analysis) 공용 안전장치.

배경(2026-09-20): 세 스크립트는 분기 단위 루프에서 예외를 잡아 "건너뜀"으로만 처리하고 끝까지 돌아, KRX가 IP를
1일간 차단해 에러 페이지를 돌려주는 상황에서도 39분기 중 25분기를 조용히 버린 채 "정상 종료"하며 부분 표본으로
통계를 출력했다(출력된 IC/표본 수를 그대로 믿으면 잘못된 결론이 나옴). 또 원자료 CSV는 루프가 다 끝난 뒤에만
저장해, 중간에 죽으면 그때까지 모은 데이터도 전부 사라졌다. 이 모듈은 그 둘을 막는다.

  - QuarterErrorGuard: 분기가 연속으로 max_consecutive번 실패하면 즉시 중단(추가 요청이 차단 기간을 연장/재적용시키지
    않도록). 중단되지 않은 실패도 끝에서 건너뛴 분기 목록을 요약해 부분 표본임을 눈에 띄게 알린다.
  - append_partial: 분기마다 원자료를 .partial.csv에 이어쓰기해 중단돼도 그때까지의 데이터가 남는다.
"""
from pathlib import Path

import pandas as pd


class QuarterErrorGuard:
    def __init__(self, max_consecutive: int = 3, partial_hint: str = ""):
        self.max_consecutive = max_consecutive
        self.partial_hint = partial_hint
        self.consecutive = 0
        self.failed: list[tuple[str, str]] = []  # [(분기 라벨, 오류 메시지)]

    def ok(self):
        """이번 분기가 정상 처리됨(데이터가 비어 있는 정상적인 '해당 없음' 포함)."""
        self.consecutive = 0

    def fail(self, label, err):
        """이번 분기가 예외로 실패함. 연속 실패가 한도에 도달하면 RuntimeError로 중단한다."""
        self.consecutive += 1
        self.failed.append((str(label), str(err)))
        if self.consecutive >= self.max_consecutive:
            raise RuntimeError(
                f"[중단] {label}까지 {self.consecutive}개 분기 연속 실패 (마지막 오류: {err}). "
                f"KRX 접속 제한/차단 가능성이 있어 추가 요청을 막기 위해 중단합니다. {self.partial_hint}".strip()
            )

    def report(self, total_quarters: int) -> int:
        """루프 종료 후 건너뛴 분기를 요약해 출력하고, 종료 코드(0=전부 정상, 2=일부 건너뜀)를 돌려준다."""
        if not self.failed:
            return 0
        print()
        print("!" * 50)
        print(f"⚠️ 경고: {total_quarters}개 분기 중 {len(self.failed)}개 분기가 오류로 건너뛰어졌습니다 — 위 통계는 부분 표본입니다.")
        for label, err in self.failed:
            print(f"   - {label}: {err}")
        print("!" * 50)
        return 2


def append_partial(path: Path, df: pd.DataFrame):
    """분기 단위 원자료를 이어쓴다(첫 쓰기에만 헤더). BOM은 이어쓰기 때마다 파일 중간에 박히므로 utf-8(BOM 없음) 사용."""
    if df is None or df.empty:
        return
    df.to_csv(path, mode="a", header=not Path(path).exists(), index=False, encoding="utf-8")
