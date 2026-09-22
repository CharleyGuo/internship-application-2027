from __future__ import annotations
import os
import sys
import shutil
import sqlite3
import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env if present
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v

DEFAULT_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]

class GoogleDriveSyncError(Exception):
    """Base exception for Google Drive sync errors."""
    pass

class CredentialsNotFoundError(GoogleDriveSyncError):
    """Raised when neither service account nor OAuth credentials could be found."""
    pass

class GDriveSync:
    """
    Synchronizes local application state (tracker.xlsx and SQLite database)
    with a Google Drive folder and live Google Spreadsheet.
    """

    def __init__(
        self,
        folder_id: Optional[str] = None,
        config_dir: Optional[Path] = None,
        project_root: Optional[Path] = None
    ):
        self.folder_id = folder_id or os.getenv("GDRIVE_FOLDER_ID") or DEFAULT_FOLDER_ID
        self.project_root = project_root or PROJECT_ROOT
        self.config_dir = config_dir or (self.project_root / "config")
        self._service = None
        self._credentials = None
        self.auth_mode: Optional[str] = None

    def get_credentials(self):
        """
        Resolve Google credentials using either:
        1. Service Account (config/service_account.json or env GDRIVE_SERVICE_ACCOUNT)
        2. Authorized user token (config/token.json)
        3. OAuth 2.0 client credentials (config/credentials.json)
        """
        if self._credentials:
            return self._credentials

        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        # 1. Check Service Account in self.config_dir first
        default_sa = self.config_dir / "service_account.json"
        sa_file = default_sa if default_sa.exists() else None

        if not sa_file and self.config_dir == (self.project_root / "config"):
            sa_env = os.getenv("GDRIVE_SERVICE_ACCOUNT")
            if sa_env:
                p = Path(sa_env)
                if not p.is_absolute():
                    p = self.project_root / p
                if p.exists() and p.is_file():
                    sa_file = p

        if sa_file and sa_file.exists():
            try:
                creds = service_account.Credentials.from_service_account_file(
                    str(sa_file), scopes=SCOPES
                )
                self._credentials = creds
                self.auth_mode = "service_account"
                self.service_account_email = getattr(creds, "service_account_email", None)
                return creds
            except Exception as e:
                raise GoogleDriveSyncError(f"Failed to load service account credentials from {sa_file}: {e}")

        # 2. Check cached OAuth token
        token_file = self.config_dir / "token.json"
        if token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    token_file.write_text(creds.to_json(), encoding="utf-8")
                if creds and creds.valid:
                    self._credentials = creds
                    self.auth_mode = "oauth_token"
                    return creds
            except Exception as e:
                # Corrupted or invalid token
                pass

        # 3. Check client_secrets / credentials.json
        client_secrets_file = self.config_dir / "credentials.json"
        if client_secrets_file.exists():
            self.auth_mode = "oauth_pending"
            return None

        raise CredentialsNotFoundError(
            f"No Google Drive credentials found.\n"
            f"Please place either:\n"
            f" • A service account key at: {self.config_dir / 'service_account.json'}\n"
            f" • OR an OAuth 2.0 Client ID at: {self.config_dir / 'credentials.json'}\n"
            f"Or run 'ia sync --setup' for setup instructions."
        )

    def run_oauth_flow(self) -> Any:
        """Run interactive OAuth consent flow to generate config/token.json."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_secrets_file = self.config_dir / "credentials.json"
        if not client_secrets_file.exists():
            raise CredentialsNotFoundError(
                f"Cannot initiate OAuth flow: '{client_secrets_file}' not found.\n"
                f"Download OAuth client ID JSON from Google Cloud Console and save it to {client_secrets_file}."
            )

        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file), SCOPES)
        creds = flow.run_local_server(port=0)
        
        token_file = self.config_dir / "token.json"
        token_file.write_text(creds.to_json(), encoding="utf-8")
        self._credentials = creds
        self.auth_mode = "oauth_token"
        return creds

    def get_service(self):
        """Build and return an authorized Google Drive API client."""
        if self._service:
            return self._service

        creds = self.get_credentials()
        if not creds:
            raise CredentialsNotFoundError("Credentials not initialized. Run OAuth setup or configure service account.")

        from googleapiclient.discovery import build
        self._service = build("drive", "v3", credentials=creds)
        return self._service

    def check_connection(self) -> Dict[str, Any]:
        """
        Verify Google Drive connectivity and check permissions on the target folder.
        """
        try:
            service = self.get_service()
        except CredentialsNotFoundError as e:
            return {
                "connected": False,
                "folder_id": self.folder_id,
                "error": str(e),
                "auth_mode": self.auth_mode or "none"
            }
        except Exception as e:
            return {
                "connected": False,
                "folder_id": self.folder_id,
                "error": f"Authentication error: {e}",
                "auth_mode": self.auth_mode or "none"
            }

        try:
            folder = service.files().get(
                fileId=self.folder_id,
                fields="id, name, mimeType, capabilities, shared, owners"
            ).execute()

            can_edit = folder.get("capabilities", {}).get("canAddChildren", False)
            return {
                "connected": True,
                "folder_id": folder.get("id"),
                "folder_name": folder.get("name", "Unknown Folder"),
                "mime_type": folder.get("mimeType"),
                "can_edit": can_edit,
                "auth_mode": self.auth_mode
            }
        except Exception as e:
            err_str = str(e)
            if "has not been used in project" in err_str or "disabled" in err_str or "accessNotConfigured" in err_str:
                import re
                urls = re.findall(r'https://console\.developers\.google\.com[^\s",]+', err_str)
                enable_url = urls[0].rstrip('."\',') if urls else "https://console.developers.google.com/apis/api/drive.googleapis.com/overview"
                return {
                    "connected": False,
                    "folder_id": self.folder_id,
                    "error": (
                        f"Google Drive API is disabled in your Google Cloud project.\n"
                        f"Please enable it by visiting:\n • {enable_url}"
                    ),
                    "auth_mode": self.auth_mode,
                    "enable_url": enable_url
                }
            return {
                "connected": False,
                "folder_id": self.folder_id,
                "error": f"Failed to access folder {self.folder_id}: {e}",
                "auth_mode": self.auth_mode
            }

    def list_folder_files(self) -> List[Dict[str, Any]]:
        """List files currently inside the target Google Drive folder."""
        service = self.get_service()
        query = f"'{self.folder_id}' in parents and trashed = false"
        res = service.files().list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime, webViewLink, size)"
        ).execute()
        return res.get("files", [])

    def sync_spreadsheet(
        self,
        local_excel_path: Optional[Path] = None,
        sheet_title: str = "Summer 2027 Internship Application Tracker"
    ) -> Dict[str, Any]:
        """
        Uploads or updates the tracker Excel file in the target folder,
        converting it into a native Google Spreadsheet.
        """
        from googleapiclient.http import MediaFileUpload

        excel_path = local_excel_path or (self.project_root / "tracker.xlsx")
        if not excel_path.exists():
            raise GoogleDriveSyncError(f"Tracker spreadsheet not found at {excel_path}. Run 'ia export' first.")

        service = self.get_service()

        # Check if Google Spreadsheet or tracker file already exists in target folder
        existing_files = self.list_folder_files()
        spreadsheet_file = None
        for f in existing_files:
            fname = f.get("name", "").lower()
            fmime = f.get("mimeType", "").lower()
            if (
                f.get("name") == sheet_title or
                fname.startswith("tracker") or
                "spreadsheet" in fmime or
                "openxmlformats" in fmime
            ):
                spreadsheet_file = f
                break

        media = MediaFileUpload(
            str(excel_path),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            resumable=True
        )

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if spreadsheet_file:
            # Update existing tracker spreadsheet in place
            file_id = spreadsheet_file["id"]
            updated = service.files().update(
                fileId=file_id,
                body={"name": "tracker.xlsx"},
                media_body=media,
                fields="id, name, webViewLink, modifiedTime"
            ).execute()

            return {
                "action": "updated",
                "file_id": updated.get("id"),
                "title": updated.get("name"),
                "web_url": updated.get("webViewLink", f"https://docs.google.com/spreadsheets/d/{file_id}/edit"),
                "synced_at": now_str,
                "folder_id": self.folder_id
            }
        else:
            try:
                # Create new converted Google Spreadsheet in the folder
                file_metadata = {
                    "name": sheet_title,
                    "parents": [self.folder_id],
                    "mimeType": "application/vnd.google-apps.spreadsheet"
                }
                created = service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, name, webViewLink, modifiedTime"
                ).execute()

                file_id = created.get("id")
                return {
                    "action": "created",
                    "file_id": file_id,
                    "title": created.get("name"),
                    "web_url": created.get("webViewLink", f"https://docs.google.com/spreadsheets/d/{file_id}/edit"),
                    "synced_at": now_str,
                    "folder_id": self.folder_id
                }
            except Exception as e:
                err_s = str(e)
                if "storageQuotaExceeded" in err_s or "quota has been exceeded" in err_s:
                    raise GoogleDriveSyncError(
                        "Google Service Accounts have 0 MB storage quota in personal @gmail.com accounts.\n"
                        "To fix: Upload tracker.xlsx once manually to the Google Drive folder:\n"
                        f"https://drive.google.com/drive/u/0/folders/{self.folder_id}\n"
                        "Once present, the service account can update it in-place on every run without consuming quota!"
                    )
                raise

    def backup_database(
        self,
        db_path: Optional[Path] = None,
        backup_filename: str = "internships_backup.db"
    ) -> Dict[str, Any]:
        """
        Creates an atomic SQLite database snapshot and uploads it to the Google Drive folder.
        """
        from googleapiclient.http import MediaFileUpload

        source_db = db_path or (self.project_root / "internships.db")
        if not source_db.exists():
            raise GoogleDriveSyncError(f"Database not found at {source_db}")

        # Create atomic SQLite snapshot in temp file
        snapshot_dir = self.project_root / "reports" / "backups"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        local_snapshot = snapshot_dir / f"internships_snapshot_{timestamp}.db"

        # Perform atomic online backup using sqlite3 backup API
        src_conn = sqlite3.connect(str(source_db))
        dst_conn = sqlite3.connect(str(local_snapshot))
        with dst_conn:
            src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()

        service = self.get_service()

        # Check if existing backup file exists in folder
        existing_files = self.list_folder_files()
        backup_file = None
        for f in existing_files:
            fname = f.get("name", "")
            if fname == backup_filename or "backup" in fname or fname.endswith(".db"):
                backup_file = f
                break

        media = MediaFileUpload(
            str(local_snapshot),
            mimetype="application/x-sqlite3",
            resumable=True
        )

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if backup_file:
            file_id = backup_file["id"]
            updated = service.files().update(
                fileId=file_id,
                media_body=media,
                fields="id, name, size, modifiedTime, webViewLink"
            ).execute()

            return {
                "action": "updated",
                "file_id": updated.get("id"),
                "filename": updated.get("name"),
                "size_bytes": updated.get("size", local_snapshot.stat().st_size),
                "synced_at": now_str,
                "local_snapshot": str(local_snapshot)
            }
        else:
            try:
                file_metadata = {
                    "name": backup_filename,
                    "parents": [self.folder_id]
                }
                created = service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, name, size, modifiedTime, webViewLink"
                ).execute()

                return {
                    "action": "created",
                    "file_id": created.get("id"),
                    "filename": created.get("name"),
                    "size_bytes": created.get("size", local_snapshot.stat().st_size),
                    "synced_at": now_str,
                    "local_snapshot": str(local_snapshot)
                }
            except Exception as e:
                err_s = str(e)
                if "storageQuotaExceeded" in err_s or "quota has been exceeded" in err_s:
                    return {
                        "action": "skipped_quota",
                        "filename": backup_filename,
                        "size_bytes": local_snapshot.stat().st_size,
                        "synced_at": now_str,
                        "local_snapshot": str(local_snapshot),
                        "message": "Database backup skipped: Service Account has 0 MB quota for creating new files in personal accounts. Upload a blank internships_backup.db to folder to enable automatic updates."
                    }
                raise
