"""V1.6 graph-backed hypothesis generation and research-question planning."""

from molecule_ranker.hypotheses.codex_assistant import CodexHypothesisAssistant
from molecule_ranker.hypotheses.codex_drafting import (
    CodexHypothesisDraft,
    CodexHypothesisDrafter,
)
from molecule_ranker.hypotheses.dashboard import (
    render_hypothesis_dashboard_html,
    write_hypothesis_dashboard,
)
from molecule_ranker.hypotheses.engine import (
    HypothesisGenerationEngine,
)
from molecule_ranker.hypotheses.evidence_gap import (
    EvidenceGapAnalyzer,
    analyze_evidence_gaps,
    analyze_evidence_gaps_for_hypotheses,
    analyze_hypothesis_evidence_gaps,
)
from molecule_ranker.hypotheses.falsification import (
    FalsificationCriteriaBuilder,
    build_falsification_criteria,
    build_falsification_criteria_for_hypotheses,
)
from molecule_ranker.hypotheses.lifecycle import HypothesisLifecycleManager
from molecule_ranker.hypotheses.planner import ResearchQuestionPlanner
from molecule_ranker.hypotheses.questions import (
    ResearchQuestionPlannerV16,
    plan_research_questions,
    plan_research_questions_for_hypotheses,
)
from molecule_ranker.hypotheses.ranking import (
    HypothesisRanker,
    RankingComponents,
    rank_hypotheses,
    rank_hypothesis,
    rank_research_hypotheses,
)
from molecule_ranker.hypotheses.review import (
    HypothesisReviewQueue,
    HypothesisReviewService,
    attach_hypotheses_to_review_workspace,
)
from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    Hypothesis,
    HypothesisCodexArtifact,
    HypothesisGenerationRun,
    HypothesisLifecycleEvent,
    HypothesisReviewDecision,
    HypothesisReviewRecord,
    HypothesisSet,
    ResearchHypothesis,
    ResearchQuestion,
    ResearchQuestionSet,
    TestableResearchQuestion,
    ValidationPlan,
)
from molecule_ranker.hypotheses.store import HypothesisStore
from molecule_ranker.hypotheses.validation import (
    HypothesisValidationError,
    detect_hypothesis_guardrail_violations,
    validate_hypothesis_references,
    validate_hypothesis_set,
)

__all__ = [
    "CodexHypothesisAssistant",
    "CodexHypothesisDraft",
    "CodexHypothesisDrafter",
    "EvidenceGap",
    "EvidenceGapAnalyzer",
    "FalsificationCriterion",
    "FalsificationCriteriaBuilder",
    "Hypothesis",
    "HypothesisCodexArtifact",
    "HypothesisGenerationEngine",
    "HypothesisRanker",
    "HypothesisGenerationRun",
    "HypothesisLifecycleEvent",
    "HypothesisLifecycleManager",
    "HypothesisReviewDecision",
    "HypothesisReviewQueue",
    "HypothesisReviewService",
    "HypothesisReviewRecord",
    "HypothesisSet",
    "HypothesisStore",
    "HypothesisValidationError",
    "ResearchHypothesis",
    "ResearchQuestion",
    "ResearchQuestionPlanner",
    "ResearchQuestionPlannerV16",
    "ResearchQuestionSet",
    "RankingComponents",
    "TestableResearchQuestion",
    "ValidationPlan",
    "analyze_evidence_gaps",
    "analyze_evidence_gaps_for_hypotheses",
    "analyze_hypothesis_evidence_gaps",
    "attach_hypotheses_to_review_workspace",
    "build_falsification_criteria",
    "build_falsification_criteria_for_hypotheses",
    "detect_hypothesis_guardrail_violations",
    "plan_research_questions",
    "plan_research_questions_for_hypotheses",
    "rank_hypothesis",
    "rank_hypotheses",
    "rank_research_hypotheses",
    "render_hypothesis_dashboard_html",
    "validate_hypothesis_references",
    "validate_hypothesis_set",
    "write_hypothesis_dashboard",
]
