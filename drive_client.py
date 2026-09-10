from __future__ import annotations

import io
import json
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


class DriveError(RuntimeError):
    pass


class DriveClient:
    def __init__(self) -> None:
        raw_credentials = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not raw_credentials:
            raise DriveError("缺少 GOOGLE_SERVICE_ACCOUNT_JSON")
        try:
            info = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            raise DriveError("GOOGLE_SERVICE_ACCOUNT_JSON 不是有效 JSON") from exc
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def list_children(self, parent_id: str, mime_type: str | None = None) -> list[dict]:
        query = [f"'{self._escape(parent_id)}' in parents", "trashed = false"]
        if mime_type:
            query.append(f"mimeType = '{self._escape(mime_type)}'")
        fields = "nextPageToken, files(id,name,mimeType,shortcutDetails(targetId,targetMimeType),modifiedTime,size)"
        token = None
        files: list[dict] = []
        while True:
            response = (
                self.service.files()
                .list(
                    q=" and ".join(query),
                    fields=fields,
                    pageSize=1000,
                    pageToken=token,
                    spaces="drive",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            files.extend(response.get("files", []))
            token = response.get("nextPageToken")
            if not token:
                return files

    def find_child_folder(self, parent_id: str, name: str) -> dict:
        folders = [
            item
            for item in self.list_children(parent_id)
            if item.get("name") == name
            and item.get("mimeType") == "application/vnd.google-apps.folder"
        ]
        if len(folders) != 1:
            raise DriveError(f"目录 {name!r} 应唯一，实际找到 {len(folders)} 个")
        return folders[0]

    def effective_folder_id(self, item: dict) -> str | None:
        if item.get("mimeType") == "application/vnd.google-apps.folder":
            return item["id"]
        if item.get("mimeType") == "application/vnd.google-apps.shortcut":
            details = item.get("shortcutDetails") or {}
            if details.get("targetMimeType") == "application/vnd.google-apps.folder":
                return details.get("targetId")
        return None

    def download(self, file_id: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with target.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

    def read_bytes(self, file_id: str) -> bytes:
        buffer = io.BytesIO()
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()

    def upsert_file(self, parent_id: str, path: Path, mime_type: str) -> str:
        existing = [item for item in self.list_children(parent_id) if item.get("name") == path.name]
        if len(existing) > 1:
            raise DriveError(f"结果目录存在多个同名文件：{path.name}")
        media = MediaFileUpload(str(path), mimetype=mime_type, resumable=False)
        if existing:
            result = (
                self.service.files()
                .update(fileId=existing[0]["id"], media_body=media, fields="id,name", supportsAllDrives=True)
                .execute()
            )
        else:
            result = (
                self.service.files()
                .create(
                    body={"name": path.name, "parents": [parent_id]},
                    media_body=media,
                    fields="id,name",
                    supportsAllDrives=True,
                )
                .execute()
            )
        return result["id"]

    def rename(self, file_id: str, new_name: str) -> None:
        self.service.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id,name",
            supportsAllDrives=True,
        ).execute()
