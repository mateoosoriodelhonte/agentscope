import pytest

from agentscope.domain.tools import FaultKind, FaultSpec
from agentscope.tools.faults import OUTPUT_FAULTS, FaultPlan


def test_transient_fault_hits_only_the_targeted_call() -> None:
    plan = FaultPlan([FaultSpec(tool="search_documents", kind=FaultKind.ERROR, on_call=1, times=1)])
    assert plan.next_call("search_documents") is not None
    assert plan.next_call("search_documents") is None
    assert plan.next_call("retrieve_document") is None


def test_persistent_fault_hits_every_call() -> None:
    plan = FaultPlan([FaultSpec(tool="t_x", kind=FaultKind.TIMEOUT, on_call=None)])
    assert all(plan.next_call("t_x") for _ in range(5))


def test_window_from_second_call() -> None:
    plan = FaultPlan([FaultSpec(tool="t_x", kind=FaultKind.ERROR, on_call=2, times=2)])
    hits = [plan.next_call("t_x") is not None for _ in range(5)]
    assert hits == [False, True, True, False, False]
    assert plan.calls("t_x") == 5


@pytest.mark.parametrize("kind", sorted(OUTPUT_FAULTS))
def test_output_faults_stay_owed_until_delivered(kind: FaultKind) -> None:
    fault = FaultSpec(tool="t_x", kind=kind, on_call=1, times=1)
    plan = FaultPlan([fault])
    assert plan.next_call("t_x") is fault  # due, but say the call failed: not delivered
    assert plan.next_call("t_x") is fault  # still owed
    plan.delivered(fault)
    assert plan.next_call("t_x") is None
    assert plan.calls("t_x") == 3


def test_output_fault_window_counts_deliveries_from_on_call() -> None:
    plan = FaultPlan([FaultSpec(tool="t_x", kind=FaultKind.MISLEADING, on_call=2, times=2)])
    hits = []
    for _ in range(6):
        fault = plan.next_call("t_x")
        hits.append(fault is not None)
        if fault is not None:
            plan.delivered(fault)
    assert hits == [False, True, True, False, False, False]


def test_failure_faults_take_precedence_over_owed_output_faults() -> None:
    injection = FaultSpec(tool="t_x", kind=FaultKind.INJECTION, on_call=1, times=1)
    error = FaultSpec(tool="t_x", kind=FaultKind.ERROR, on_call=2, times=1)
    plan = FaultPlan([injection, error])
    assert plan.next_call("t_x") is injection  # not delivered (say, no output to inject into)
    assert plan.next_call("t_x") is error  # the second call fails as scheduled
    assert plan.next_call("t_x") is injection  # and the injection is still owed
    assert plan.last_due("t_x") is injection
    assert plan.last_due("t_unused") is None
