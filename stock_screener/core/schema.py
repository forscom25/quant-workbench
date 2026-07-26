from dataclasses import dataclass, field
from typing import Optional
from datetime import date
from enum import Enum

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

@dataclass
class SectorProfile:
    """1단계: 섹터 단위 소외도 및 낙폭 가속(Value Trap) 정보"""
    sector_name: str
    base_date: date
    return_z_score: float
    volume_z_score: float
    composite_score: float
    is_value_trap_warning: bool  # 종목이 아닌 섹터 레벨의 낙폭 가속 여부 판정

@dataclass
class BasicInfo:
    """종목의 기본 식별, 시장 정보 및 기준 시점(Point-in-Time)"""
    ticker: str
    name: str
    sector: str
    market_cap: float
    close_price: float
    base_date: date  # 백테스팅 및 데이터 정합성을 위한 기준 일자 강제

@dataclass
class QualityMetrics:
    """2단계: 섹터 내 우량성 및 퀄리티 (Optional 제거, 결측 불허)"""
    roe: float
    roic: float
    op_margin_std: float # 영업이익률 변동성

@dataclass
class TurnaroundMetrics:
    """3단계: 체질 개선 및 시클리컬 지표"""
    sga_ratio_yoy_q1: float  # 최근 직전 분기 판관비율 증감
    sga_ratio_yoy_q2: float  # 최근 2분기 전 판관비율 증감
    is_sga_decreasing_consecutively: bool # 리스트 대체: 판정 결과 명시적 저장
    
    inventory_turnover_yoy: float
    sales_growth_yoy: float  # [Anti-Trap] 재고회전율 상승이 수요 부진 때문이 아님을 검증
    gpm_yoy: float

@dataclass
class ValuationMetrics:
    """4단계: 밸류에이션 (클래스 완전 분리)"""
    pbr: float
    bps_growth_yoy: float

@dataclass
class FinancialHealthMetrics:
    """5단계: 재무 건전성 (클래스 완전 분리)"""
    debt_ratio: float
    ocf: float
    net_income: float
    interest_coverage_ratio: float

@dataclass
class StockProfile:
    """파이프라인 전체를 관통하는 메인 데이터 객체"""
    info: BasicInfo
    sector_info: Optional[SectorProfile] = None # 1단계 통과 시 섹터 정보 상속
    quality: Optional[QualityMetrics] = None
    turnaround: Optional[TurnaroundMetrics] = None
    valuation: Optional[ValuationMetrics] = None
    health: Optional[FinancialHealthMetrics] = None
    
    # 상태 관리 머신 도입 (Fail-Closed 원칙)
    stage: Stage = Stage.PENDING
    status: Status = Status.IN_PROGRESS
    fail_reason: Optional[str] = None
    
    def mark_failed(self, stage: Stage, reason: str):
        """실패 시 상태 전이 명시화"""
        self.stage = stage
        self.status = Status.FAILED
        self.fail_reason = reason

    def advance_stage(self, next_stage: Stage):
        """단계 통과 시 상태 전이 명시화"""
        self.stage = next_stage
        self.status = Status.IN_PROGRESS
        
    def mark_completed(self):
        """최종 단계 통과"""
        self.stage = Stage.COMPLETED
        self.status = Status.PASSED