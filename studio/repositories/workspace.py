import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES


class JsonWorkspaceRepository:
    def __init__(self, workspace_dir=None):
        self.workspace_dir = Path(workspace_dir or settings.WORKSPACE_DIR)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, genre, workspace_id=None):
        if genre not in GENRES:
            raise ValueError(f"Unknown genre: {genre}")

        now = self._now()
        workspace = {
            "id": workspace_id or now.replace(":", "").replace("-", "").replace("+", "")[:15],
            "genre": genre,
            "episode_count": EPISODE_COUNT,
            "episode_duration_minutes": EPISODE_DURATION_MINUTES,
            "outlines": [],
            "selected_outline_id": None,
            "script_plan": [],
            "episode_1_script": "",
            "storyboard_prompts": [],
            "created_at": now,
            "updated_at": now,
        }
        return self.save_workspace(workspace)

    def get_workspace(self, workspace_id):
        path = self.path_for(workspace_id)
        if not path.exists():
            raise FileNotFoundError(f"Workspace not found: {workspace_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save_workspace(self, workspace):
        workspace = dict(workspace)
        workspace["updated_at"] = self._now()
        self.path_for(workspace["id"]).write_text(
            json.dumps(workspace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return workspace

    def update_workspace(self, workspace_id, **fields):
        workspace = self.get_workspace(workspace_id)
        workspace.update(fields)
        return self.save_workspace(workspace)

    def path_for(self, workspace_id):
        safe_id = str(workspace_id).replace("/", "").replace("\\", "")
        return self.workspace_dir / f"{safe_id}.json"

    @staticmethod
    def _now():
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
