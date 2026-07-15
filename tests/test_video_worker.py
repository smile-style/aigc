from io import StringIO
from types import SimpleNamespace

from studio.management.commands.process_video_tasks import Command


def test_worker_continues_after_individual_task_failure():
    command = Command()
    command.stderr = StringIO()
    processed_ids = []
    recorded = []

    def processor(item_id):
        processed_ids.append(item_id)
        if item_id == 1:
            raise RuntimeError("broken task")

    count = command._process_items(
        [SimpleNamespace(id=1), SimpleNamespace(id=2)],
        processor,
        lambda item, error: recorded.append((item.id, str(error))),
        "video asset",
    )

    assert count == 2
    assert processed_ids == [1, 2]
    assert recorded == [(1, "broken task")]
    assert "Failed video asset 1: broken task" in command.stderr.getvalue()


def test_worker_continues_when_recording_failure_also_fails():
    command = Command()
    command.stderr = StringIO()
    processed_ids = []

    def processor(item_id):
        processed_ids.append(item_id)
        if item_id == 1:
            raise RuntimeError("broken task")

    def failure_recorder(item, error):
        raise RuntimeError("database unavailable")

    command._process_items(
        [SimpleNamespace(id=1), SimpleNamespace(id=2)],
        processor,
        failure_recorder,
        "video export task",
    )

    assert processed_ids == [1, 2]
