from dataclasses import dataclass, fields
from types import SimpleNamespace
from typing import Optional
from enum import Enum, IntEnum

# =========================================================
# 1. Pipeline Control Enums (파이프라인 제어용 상태값)
# =========================================================
class Stage(IntEnum):
    """단계 간 순서 비교(<=, <, >) 가능 (stop_after 로직 지원)"""
    STAGE1 = 1
    STAGE2 = 2
    STAGE3 = 3
    STAGE4 = 4
    STAGE5 = 5

class FailReason(Enum):
    """DataFrame의 fail_reason 컬럼에 들어갈 규격화된 탈락 사유"""
    SECTOR_NOT_QUALIFIED = "sector_not_qualified"
    QUALITY_CUTOFF_NOT_MET = "quality_cutoff_not_met"
    COMPOSITE_SCORE_BELOW_CUTOFF = "composite_score_below_cutoff"
    DATA_TOO_SHORT = "data_too_short"
    CRITICAL_METRIC_NOT_COMPUTABLE = "critical_metric_not_computable"

class MetricStatus(Enum):
    """na_reasons 딕셔너리에 사용될 연산 상태"""
    COMPUTED = "computed"
    NOT_COMPUTABLE = "not_computable"
    CAUTION = "caution"

# =========================================================
# 2. DataFrame Schema Contracts (데이터프레임 컬럼 명세서)
# =========================================================
@dataclass
class SectorMetrics:
    """Stage 1 (소외 섹터 발굴) 출력 DataFrame 스키마 명세"""
    sector: str
    return_z_score: float
    volume_z_score: float
    composite_score: float
    is_value_trap_warning: bool
    fail_reason: Optional[str] = None

@dataclass
class QualityMetrics:
    """Stage 2 (섹터 내 우량주 탐색) 출력 DataFrame 스키마 명세"""
    roe: float
    roic: float
    op_margin_std: float
    na_reasons: str = ""
    fail_reason: Optional[str] = None

@dataclass
class TurnaroundMetrics:
    """Stage 3 (턴어라운드) 출력 DataFrame 스키마 명세"""
    sga_yoy_avg: float
    sales_growth_yoy: float
    gpm_yoy: float
    stage3_score: float
    is_cost_cutting_warning: bool
    inventory_turnover_yoy: Optional[float] = None
    # 2026-09-10: dict -> comma-joined string으로 통일(Stage2/4/5와 동일 타입). 어느 stage도
    # MetricStatus/설명 문구를 na_reasons에서 되읽은 적이 없어(항상 태그명 문자열 포함 여부만
    # 확인) 손실 없이 단순화, 겹치는 컬럼명이 stage 간 병합될 때 타입 불일치로 인한 위험도 제거.
    na_reasons: str = ""
    # 현금흐름 구제(penalty 상쇄) 판단용 — 매출은 늘지만 판관비 증가·마진 하락으로 컷오프
    # 미달인 종목도, OCF가 건실하면(테마성 부풀리기가 아니라 실제 현금이 들어오면) 구제한다.
    ocf: Optional[float] = None
    net_income: Optional[float] = None

@dataclass
class ValuationMetrics:
    """Stage 4 (밸류에이션) 출력 DataFrame 스키마 명세"""
    pbr: float
    per: Optional[float]
    bps_growth: float
    stage4_score: float
    is_pbr_value_trap: bool
    na_reasons: str = ""

@dataclass
class FinancialHealthMetrics:
    """Stage 5 (재무 건전성) 출력 DataFrame 스키마 명세"""
    debt_ratio: float
    ocf: float
    net_income: float
    interest_coverage_ratio: float
    stage5_score: float
    warning_tags: str = ""
    na_reasons: str = ""
    fail_reason: Optional[str] = None

# =========================================================
# 3. Dynamic Column Accessors (컬럼명 자동 완성 네임스페이스)
# =========================================================
# dataclass의 필드명으로부터 문자열 상수를 자동 생성 (이중 타이핑 오타 방지)
SectorCols = SimpleNamespace(**{f.name: f.name for f in fields(SectorMetrics)})
QualityCols = SimpleNamespace(**{f.name: f.name for f in fields(QualityMetrics)})
TurnaroundCols = SimpleNamespace(**{f.name: f.name for f in fields(TurnaroundMetrics)})
ValuationCols = SimpleNamespace(**{f.name: f.name for f in fields(ValuationMetrics)})
HealthCols = SimpleNamespace(**{f.name: f.name for f in fields(FinancialHealthMetrics)})

# =========================================================
# 4. Validation Helpers (데이터프레임 무결성 검증 도구)
# =========================================================
def validate_schema(df, dataclass_type) -> bool:
    """DataFrame이 해당 dataclass에 정의된 모든 컬럼을 포함하는지 검증합니다."""
    required_cols = {f.name for f in fields(dataclass_type)}
    missing_cols = required_cols - set(df.columns)
    
    assert not missing_cols, f"❌ 스키마 검증 실패: 다음 컬럼이 누락되었습니다 -> {missing_cols}"
    return True