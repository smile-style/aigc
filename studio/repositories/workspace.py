import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

from studio.constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES

WORKSPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CURRENT_WORKSPACE_ID = "current"


class WorkspaceCorruptError(Exception):
    pass


class JsonWorkspaceRepository:
    def __init__(self, workspace_dir=None):
        self.workspace_dir = Path(workspace_dir or settings.WORKSPACE_DIR)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, genre, workspace_id=None):
        if genre not in GENRES:
            raise ValueError(f"Unknown genre: {genre}")

        now = self._now()
        workspace = {
            "id": workspace_id or uuid.uuid4().hex,
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
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkspaceCorruptError(f"Workspace corrupt: {workspace_id}") from exc

    def save_workspace(self, workspace):
        workspace = dict(workspace)
        workspace["updated_at"] = self._now()
        path = self.path_for(workspace["id"])
        temp_path = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(workspace, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return workspace

    def get_current_workspace(self):
        return self.get_workspace(CURRENT_WORKSPACE_ID)

    def replace_current_outline_set(self, genre, outlines):
        workspace = self.create_workspace(genre, workspace_id=CURRENT_WORKSPACE_ID)
        return self.update_workspace(
            workspace["id"],
            outlines=outlines,
            selected_outline_id=None,
            script_plan=[],
            episode_1_script="",
            storyboard_prompts=[],
        )

    def update_workspace(self, workspace_id, **fields):
        workspace = self.get_workspace(workspace_id)
        workspace.update(fields)
        return self.save_workspace(workspace)

    def path_for(self, workspace_id):
        workspace_id = str(workspace_id)
        if not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
            raise ValueError(f"Invalid workspace id: {workspace_id}")
        return self.workspace_dir / f"{workspace_id}.json"

    @staticmethod
    def _now():
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
