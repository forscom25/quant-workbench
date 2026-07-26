from dataclasses import dataclass, field
from typing import Optional
from datetime import date, datetime
from enum import Enum

# ==========================================
# 1. 상태 및 메타데이터 열거형(Enum) 정의
# ==========================================

class Stage(Enum):
    PENDING = 0
    NEGLECT_SECTOR = 1
    QUALITY = 2
    TURNAROUND = 3
    VALUATION = 4
    FINANCIAL_HEALTH = 5
    COMPLETED = 6

class Status(Enum):
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    DATA_INJECTED = "data_injected"  # 외부 데이터 주입 등 특수 이벤트 로깅용 추가

class MetricStatus(Enum):
    COMPUTED = "computed"              # 정상 계산됨
    NOT_COMPUTABLE = "not_computable"  # 계산 불가 (결측/정의 불가, float('nan') 할당 기준)
    CAUTION = "caution"                # 처리 주의 (계산은 됐으나 해석 유의)

# ==========================================
# 2. 상태 이벤트 로그 구조 (ROS 패턴)
# ==========================================

@dataclass
class StageEvent:
    stage: Stage
    status: Status
    timestamp: datetime = field(default_factory=datetime.now)

# ==========================================
# 3. 단계별 데이터 파이프라인 규격(Metrics)
# ==========================================

@dataclass
class SectorProfile:
    """1단계: 섹터 단위 소외도 및 낙폭 가속(Value Trap) 정보"""
    sector_name: str
    base_date: date
    return_z_score: float
    volume_z_score: float
    composite_score: float
    is_value_trap_warning: bool

@dataclass
class QualityMetrics:
    """2단계: 섹터 내 우량성 및 실적 안정성"""
    roe: float
    roic: float
    op_margin_std: float 
    na_reasons: dict[str, tuple[MetricStatus, Optional[str]]] = field(default_factory=dict)

@dataclass
class TurnaroundMetrics:
    """3단계: 체질 개선 및 시클리컬 지표"""
    sga_ratio_yoy_q1: float  
    sga_ratio_yoy_q2: float  
    is_sga_decreasing_consecutively: bool 
    
    inventory_turnover_yoy: float
    sales_growth_yoy: float
    gpm_yoy: float
    na_reasons: dict[str, tuple[MetricStatus, Optional[str]]] = field(default_factory=dict)

@dataclass
class ValuationMetrics:
    """4단계: 밸류에이션"""
    pbr: float
    bps_growth_yoy: float
    na_reasons: dict[str, tuple[MetricStatus, Optional[str]]] = field(default_factory=dict)

@dataclass
class FinancialHealthMetrics:
    """5단계: 재무 건전성 및 이익 품질"""
    debt_ratio: float
    ocf: float               
    net_income: float
    interest_coverage_ratio: float
    na_reasons: dict[str, tuple[MetricStatus, Optional[str]]] = field(default_factory=dict)

# ==========================================
# 4. 파이프라인 메인 데이터 객체
# ==========================================

@dataclass
class BasicInfo:
    """종목의 기본 식별, 시장 정보 및 기준 시점"""
    ticker: str
    name: str
    sector: str
    market_cap: float
    close_price: float
    base_date: date

@dataclass
class StockProfile:
    """파이프라인 전체를 관통하는 메인 데이터 규격 (State Machine)"""
    info: BasicInfo
    sector_info: Optional[SectorProfile] = None 
    quality: Optional[QualityMetrics] = None
    turnaround: Optional[TurnaroundMetrics] = None
    valuation: Optional[ValuationMetrics] = None
    health: Optional[FinancialHealthMetrics] = None
    
    # 이벤트 스트림 이력 관리
    history: list[StageEvent] = field(default_factory=list)
    
    # 파이프라인 전체 탈락 사유 독립 보존 (추후 FailReason Enum 고도화 가능 지점)
    fail_reason: Optional[str] = None
    
    # [명확화] 데이터 시차(Lag) 관리: 종목 기준일과 섹터 기준일의 차이
    sector_data_lag_days: Optional[int] = None
    
    def __post_init__(self):
        """객체 생성 시 PENDING 상태를 이력의 첫 줄에 기록"""
        if not self.history:
            self._record_event(Stage.PENDING, Status.IN_PROGRESS)

    def _record_event(self, stage: Stage, status: Status):
        """내부 메서드: 상태 전이 이벤트를 로그에 Append"""
        self.history.append(StageEvent(stage=stage, status=status))

    def inject_sector_info(self, sector_info: SectorProfile):
        """섹터 정보 주입 시 미래 데이터(Look-ahead bias) 침투 방어 및 로깅"""
        if sector_info.base_date > self.info.base_date:
            raise ValueError(
                f"[미래 데이터 침투] 섹터 기준일({sector_info.base_date})이 "
                f"종목 기준일({self.info.base_date})보다 미래일 수 없습니다."
            )
        self.sector_info = sector_info
        self.sector_data_lag_days = (self.info.base_date - sector_info.base_date).days
        
        # 하드코딩 제거: 현재 시점의 stage를 동적으로 추적하여 이벤트 기록
        current_stage = self.history[-1].stage if self.history else Stage.PENDING
        self._record_event(current_stage, Status.DATA_INJECTED)

    def advance_stage(self, next_stage: Stage):
        """현재 단계를 PASSED로 마감하고 다음 단계를 IN_PROGRESS로 시작"""
        if self.history:
            current_stage = self.history[-1].stage
            self._record_event(current_stage, Status.PASSED)
        
        self._record_event(next_stage, Status.IN_PROGRESS)

    def mark_failed(self, reason: str):
        """현재 단계에서 탈락(FAILED) 처리 및 사유를 전용 필드에 저장"""
        if self.history:
            current_stage = self.history[-1].stage
            self._record_event(current_stage, Status.FAILED)
        
        # MetricStatus(CAUTION) 오염 방지를 위해 전용 임시 필드 사용
        self.fail_reason = reason

    def mark_completed(self):
        """전체 파이프라인 종결"""
        if self.history:
            current_stage = self.history[-1].stage
            self._record_event(current_stage, Status.PASSED)
            
        self._record_event(Stage.COMPLETED, Status.PASSED)