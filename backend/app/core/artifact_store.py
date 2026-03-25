from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


STAGE_NAMES = ("import", "split", "analyze", "mark", "rewrite", "assemble")


@dataclass(slots=True)
class OrphanArtifact:
    novel_id: str
    task_id: str
    reason: str
    path: str


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def novels_root(self) -> Path:
        return self.root / "novels"

    def novel_dir(self, novel_id: str) -> Path:
        return self.novels_root / novel_id

    def tasks_dir(self, novel_id: str) -> Path:
        return self.novel_dir(novel_id) / "tasks"

    def import_dir(self, novel_id: str) -> Path:
        return self.novel_dir(novel_id) / "import"

    def import_assets_dir(self, novel_id: str) -> Path:
        return self.import_dir(novel_id) / "assets"

    def task_dir(self, novel_id: str, task_id: str) -> Path:
        return self.tasks_dir(novel_id) / task_id

    def stage_dir(self, novel_id: str, task_id: str, stage: str) -> Path:
        return self.task_dir(novel_id, task_id) / "stages" / stage

    def stage_runs_dir(self, novel_id: str, task_id: str, stage: str) -> Path:
        return self.stage_dir(novel_id, task_id, stage) / "runs"

    def stage_run_dir(self, novel_id: str, task_id: str, stage: str, run_seq: int) -> Path:
        return self.stage_runs_dir(novel_id, task_id, stage) / str(run_seq)

    def stage_run_latest_dir(self, novel_id: str, task_id: str, stage: str) -> Path:
        return self.stage_dir(novel_id, task_id, stage) / "latest"

    def stage_run_manifest_path(self, novel_id: str, task_id: str, stage: str, run_seq: int) -> Path:
        return self.stage_run_dir(novel_id, task_id, stage, run_seq) / "run.json"

    def stage_run_latest_manifest_path(self, novel_id: str, task_id: str, stage: str) -> Path:
        return self.stage_run_latest_dir(novel_id, task_id, stage) / "run.json"

    def ensure_base_dirs(self) -> None:
        self.novels_root.mkdir(parents=True, exist_ok=True)

    def ensure_novel_dirs(self, novel_id: str) -> Path:
        novel_dir = self.novel_dir(novel_id)
        (novel_dir / "tasks").mkdir(parents=True, exist_ok=True)
        self.import_assets_dir(novel_id).mkdir(parents=True, exist_ok=True)
        return novel_dir

    def ensure_import_dir(self, novel_id: str) -> Path:
        import_dir = self.import_dir(novel_id)
        import_dir.mkdir(parents=True, exist_ok=True)
        self.import_assets_dir(novel_id).mkdir(parents=True, exist_ok=True)
        return import_dir

    def ensure_task_scaffold(self, novel_id: str, task_id: str) -> Path:
        task_dir = self.task_dir(novel_id, task_id)
        for stage_name in STAGE_NAMES:
            (task_dir / "stages" / stage_name).mkdir(parents=True, exist_ok=True)
        return task_dir

    def active_task_file(self, novel_id: str) -> Path:
        return self.novel_dir(novel_id) / "active_task_id"

    def read_active_task_id(self, novel_id: str) -> str | None:
        active_file = self.active_task_file(novel_id)
        if not active_file.exists():
            return None
        value = active_file.read_text(encoding="utf-8").strip()
        return value or None

    def write_active_task_id(self, novel_id: str, task_id: str) -> None:
        novel_dir = self.ensure_novel_dirs(novel_id)
        (novel_dir / "active_task_id").write_text(task_id, encoding="utf-8")

    def detect_orphans(self, novel_id: str | None = None) -> list[OrphanArtifact]:
        if not self.novels_root.exists():
            return []

        novels = [self.novel_dir(novel_id)] if novel_id else [p for p in self.novels_root.iterdir() if p.is_dir()]
        orphans: list[OrphanArtifact] = []

        for novel_path in novels:
            active_task_id = None
            active_file = novel_path / "active_task_id"
            if active_file.exists():
                active_task_id = active_file.read_text(encoding="utf-8").strip() or None

            tasks_root = novel_path / "tasks"
            if not tasks_root.exists():
                continue

            for task_path in tasks_root.iterdir():
                if not task_path.is_dir():
                    continue
                task_id = task_path.name
                if active_task_id and task_id == active_task_id:
                    continue
                status_file = task_path / "stages"
                if not status_file.exists():
                    orphans.append(
                        OrphanArtifact(
                            novel_id=novel_path.name,
                            task_id=task_id,
                            reason="missing_stages_directory",
                            path=str(task_path),
                        )
                    )
                    continue
                if self._looks_incomplete(task_path):
                    orphans.append(
                        OrphanArtifact(
                            novel_id=novel_path.name,
                            task_id=task_id,
                            reason="unlinked_or_incomplete_task",
                            path=str(task_path),
                        )
                    )
        return orphans

    def _looks_incomplete(self, task_path: Path) -> bool:
        for stage_name in STAGE_NAMES:
            stage_path = task_path / "stages" / stage_name
            if not stage_path.exists():
                return True
        return False

    def ensure_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
