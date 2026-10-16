from agentscope.domain.tools import FaultKind, FaultSpec
from agentscope.tools.faults import FaultPlan


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
