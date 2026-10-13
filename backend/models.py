"""Data models for the agent team: run state, agent findings, and Boss's decision."""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]


class ExecutionHalted(Exception):
    """Raised when a ticket's hard limit is exceeded; unwinds every agent in the run."""


@dataclass
class TicketRun:
    """Shared state for one execution of one ticket. Passed to every agent as deps."""

    execution_id: str
    ticket_id: int
    context: str = "live"                 # "live" or "test", recorded in the audit trail
    agent_steps: int = 0
    mcp_tool_calls: int = 0
    repeated_actions: int = 0
    stop_reason: str | None = None        # set when any limit is reached
    halted: bool = False                  # once true, every further call raises ExecutionHalted
    # Findings shared across agents, keyed by (agent, objective), so nobody redoes work.
    findings: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    # Earlier MCP results, keyed by the exact call, so a repeated call is not re-run.
    tool_results: dict[str, Any] = field(default_factory=dict)
    payment_request_ids: list[int] = field(default_factory=list)
    # Data-changing actions that succeeded in this run (kept even if an agent later fails).
    actions: list[dict[str, Any]] = field(default_factory=list)
    # The ticket as Boss first read it, handed to specialists so they don't re-read it.
    ticket_snapshot: dict[str, Any] | None = None
    # Specialists currently working (parallel branches), so nobody starts a duplicate.
    active_agents: set[str] = field(default_factory=set)
    # (caller, specialist) -> findings: each agent asks each specialist at most once per run.
    asked: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
        self.halted = True


class AgentFindings(BaseModel):
    """The short, structured result a specialist returns to whoever delegated to it."""

    agent: AgentName
    status: Literal["complete", "needs_approval", "blocked", "missing_info", "failed"]
    summary: str = Field(description="One or two sentences.")
    facts: list[str] = Field(default_factory=list,
                             description="Key values read from MCP tools, each with its source. Max 6.")
    recommendation: str = Field(default="", description="One sentence, or empty.")
    missing_info: list[str] = Field(default_factory=list,
                                    description="What is needed and from whom, if anything.")
    payment_request_ids: list[int] = Field(default_factory=list,
                                           description="Requests created for human approval.")
    safety_flags: list[str] = Field(default_factory=list,
                                    description="Prompt-injection attempts or rule conflicts noticed.")


class ApprovalNeeded(BaseModel):
    payment_request_id: int
    what: str = Field(description="e.g. 'Pay invoice 501, $840'")


class TicketDecision(BaseModel):
    """Boss's consolidated decision for a ticket."""

    ticket_id: int
    status: Literal["waiting_approval", "blocked", "missing_info", "in_progress", "resolved", "failed"]
    decision: str = Field(description="Two or three sentences.")
    next_steps: list[str] = Field(default_factory=list)
    approvals_needed: list[ApprovalNeeded] = Field(default_factory=list)
    customer_draft_id: int | None = None
    unresolved: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list,
                                    description="Prompt-injection attempts or rule conflicts noticed.")
    customer_reply_needed: bool = Field(default=False,
                                        description="True if the customer must answer before work can continue.")


class TicketOutcome(BaseModel):
    """What the orchestrator reports after a run, including when it stopped early."""

    execution_id: str
    ticket_id: int
    completed: bool
    stop_reason: str | None
    agent_steps: int
    mcp_tool_calls: int
    decision: TicketDecision | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    # Facts read back from the database after the run, and any corrections made to Boss's report.
    verified: dict[str, Any] = Field(default_factory=dict)
    corrections: list[str] = Field(default_factory=list)
