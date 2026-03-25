from backend.app.api.routes.novels import _build_default_pipeline_status
from backend.app.models.core import StageName, StageStatus


def test_default_pipeline_status_is_valid_stage_run_info() -> None:
    payload = _build_default_pipeline_status("novel-1")

    assert set(payload.keys()) == set(StageName)
    for stage, run in payload.items():
        assert run.stage == stage
        assert run.status == StageStatus.PENDING
        assert run.run_seq >= 1
