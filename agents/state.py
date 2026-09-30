from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, NotRequired
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class TestPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TestType(str, Enum):
    API = "api"
    UI = "ui"
    INTEGRATION = "integration"
    CHAOS = "chaos"
    PERFORMANCE = "performance"
    UNIT = "unit"


class TestCaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    ERROR = "error"
    UNTESTED = "untested"


class TestStep(BaseModel):
    step: int
    action: str
    data: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ExpectedResult(BaseModel):
    status_code: int | None = None
    body: dict[str, Any] = Field(default_factory=dict)          # key checks / jsonpath-like
    headers: dict[str, Any] = Field(default_factory=dict)
    side_effects: list[str] = Field(default_factory=list)
    response_time_ms_max: int | None = None


class TestCase(BaseModel):
    """Test Case theo hướng chuẩn IEEE 829 / ISTQB (rút gọn thực dụng)."""

    # 1. Identification & Metadata
    id: str                                                     # TC_AUTH_LOGIN_001
    title: str                                                  # Tên ngắn gọn mục tiêu
    description: str = ""
    module: str = ""                                            # Authentication / Payment...
    requirement_id: str | None = None                           # US-102, REQ_PAYMENT_201
    priority: TestPriority = TestPriority.MEDIUM
    type: TestType = TestType.API
    author: str = "QC-Agent"
    tags: list[str] = Field(default_factory=list)

    # 2. Setup & Input
    preconditions: list[str] = Field(default_factory=list)
    test_data: dict[str, Any] = Field(default_factory=dict)
    environment: str = "staging"

    # 3. Execution & Verification
    steps: list[TestStep] = Field(default_factory=list)
    expected: ExpectedResult = Field(default_factory=ExpectedResult)

    # 4. Outcome & Closure (điền khi execute)
    status: TestCaseStatus = TestCaseStatus.UNTESTED
    actual_result: str | None = None
    error_message: str | None = None
    defect_id: str | None = None
    postconditions: list[str] = Field(default_factory=list)
    duration_ms: float | None = None
    artifacts: list[str] = Field(default_factory=list)


class TestPlan(BaseModel):
    title: str
    summary: str = ""
    scope: str = "api"                                          # Phase 1 ưu tiên api
    priority_order: list[str] = Field(default_factory=list)
    test_cases: list[TestCase] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    estimated_duration_min: int | None = None
    created_by: str = "QC-Agent-Planner"


class ExecutionResult(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    error: int = 0
    untested: int = 0
    duration_ms: float = 0
    details: list[dict[str, Any]] = Field(default_factory=list)


class AgentState(TypedDict):
    """Shared state across the entire QC Agent graph."""

    # Input
    user_request: str
    documents: list[str]
    code_paths: list[str]
    openapi_spec: str | None

    # Intermediate
    messages: Annotated[list, add_messages]
    test_plan: dict | None
    generated_tests: list[dict]
    human_approved: bool
    shared_context: dict  # Hybrid: token/cookie từ API → UI

    # Output
    execution_result: dict | None
    report_path: str | None
    final_summary: str | None

    # Control
    current_step: str
    error: str | None
    ui_headed: bool  # Phase 2: chạy browser headed

    # TUI (optional — main.py không cần điền)
    # NotRequired là qualifier thật: LangGraph đọc schema bằng
    # get_type_hints(..., include_extras=True) nên vẫn coi 2 key này là optional.
    phases: NotRequired[list[str]]   # các phase được chọn: api/ui/chaos/performance
    job_id: NotRequired[str]        # uuid4, dùng làm thread_id cho LangGraph
