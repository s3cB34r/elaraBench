"""The neutral primitive preserves the established Recovery entry points."""

from elarabench.action_compliance import AuthorizationState
from elarabench.action_recovery import ActionRecoveryConfig
from elarabench.action_recovery_corpus import analyze_bounded_recoverability
from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import get_builtin_suite_path
from elarabench.synthetic_reachability import analyze_bounded_reachability


def test_all_production_recovery_proofs_match_neutral_primitive() -> None:
    suite = load_benchmark_suite(get_builtin_suite_path("action_recovery.core"))
    for case in suite.suite.cases:
        config = ActionRecoveryConfig.model_validate(case.evaluation.config)
        if config.authorization is not AuthorizationState.AUTHORIZED:
            continue
        assert config.expected_state is not None
        assert analyze_bounded_recoverability(config) == analyze_bounded_reachability(
            config.tools, config.resulting_state, config.expected_state,
            config.max_plan_length, config._action_view(),
        )
