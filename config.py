from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    root_folder_id: str
    pending_folder_name: str = "待拆图纸"
    processing_folder_name: str = "正在加工"
    template_folder_name: str = "拆图模版"
    result_folder_name: str = "拆图结果"
    completed_prefix: str = "完成_"

    @classmethod
    def from_env(cls) -> "Settings":
        root_id = os.environ.get("DRIVE_ROOT_FOLDER_ID", "").strip()
        if not root_id:
            raise RuntimeError("缺少 DRIVE_ROOT_FOLDER_ID")
        return cls(root_folder_id=root_id)

