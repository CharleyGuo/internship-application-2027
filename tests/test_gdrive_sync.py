import os
import sys
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.gdrive_sync import GDriveSync, CredentialsNotFoundError, GoogleDriveSyncError

@pytest.fixture
def mock_project_env(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    # Create dummy db
    db_file = tmp_path / "internships.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test (id int, name text)")
    conn.execute("INSERT INTO test VALUES (1, 'sample')")
    conn.commit()
    conn.close()

    # Create dummy tracker.xlsx
    xlsx_file = tmp_path / "tracker.xlsx"
    xlsx_file.write_bytes(b"dummy xlsx binary content")

    return {
        "root": tmp_path,
        "config_dir": config_dir,
        "db_file": db_file,
        "xlsx_file": xlsx_file
    }

def test_gdrive_sync_init_and_default_folder():
    syncer = GDriveSync(folder_id="test_custom_folder_123")
    assert syncer.folder_id == "test_custom_folder_123"

def test_gdrive_sync_credentials_not_found(mock_project_env):
    syncer = GDriveSync(
        folder_id="test_folder_123",
        config_dir=mock_project_env["config_dir"],
        project_root=mock_project_env["root"]
    )
    with pytest.raises(CredentialsNotFoundError) as exc_info:
        syncer.get_credentials()
    assert "No Google Drive credentials found" in str(exc_info.value)

def test_check_connection_unauthenticated(mock_project_env):
    syncer = GDriveSync(
        folder_id="test_folder_123",
        config_dir=mock_project_env["config_dir"],
        project_root=mock_project_env["root"]
    )
    res = syncer.check_connection()
    assert res["connected"] is False
    assert res["folder_id"] == "test_folder_123"
    assert "No Google Drive credentials found" in res["error"]

def test_gdrive_sync_spreadsheet_create_flow(mock_project_env):
    syncer = GDriveSync(
        folder_id="test_folder_123",
        config_dir=mock_project_env["config_dir"],
        project_root=mock_project_env["root"]
    )

    mock_service = MagicMock()
    # Mock list: folder is currently empty
    mock_service.files().list().execute.return_value = {"files": []}
    # Mock create: returns created file info
    mock_service.files().create().execute.return_value = {
        "id": "mock_sheet_id_456",
        "name": "Summer 2027 Internship Application Tracker",
        "webViewLink": "https://docs.google.com/spreadsheets/d/mock_sheet_id_456/edit"
    }

    with patch.object(syncer, "get_service", return_value=mock_service):
        res = syncer.sync_spreadsheet(local_excel_path=mock_project_env["xlsx_file"])
        assert res["action"] == "created"
        assert res["file_id"] == "mock_sheet_id_456"
        assert "docs.google.com/spreadsheets" in res["web_url"]

def test_gdrive_sync_spreadsheet_update_flow(mock_project_env):
    syncer = GDriveSync(
        folder_id="test_folder_123",
        config_dir=mock_project_env["config_dir"],
        project_root=mock_project_env["root"]
    )

    mock_service = MagicMock()
    # Mock list: existing spreadsheet already found
    mock_service.files().list().execute.return_value = {
        "files": [{
            "id": "existing_sheet_789",
            "name": "Summer 2027 Internship Application Tracker",
            "mimeType": "application/vnd.google-apps.spreadsheet"
        }]
    }
    # Mock update
    mock_service.files().update().execute.return_value = {
        "id": "existing_sheet_789",
        "name": "Summer 2027 Internship Application Tracker",
        "webViewLink": "https://docs.google.com/spreadsheets/d/existing_sheet_789/edit"
    }

    with patch.object(syncer, "get_service", return_value=mock_service):
        res = syncer.sync_spreadsheet(local_excel_path=mock_project_env["xlsx_file"])
        assert res["action"] == "updated"
        assert res["file_id"] == "existing_sheet_789"

def test_gdrive_backup_database_flow(mock_project_env):
    syncer = GDriveSync(
        folder_id="test_folder_123",
        config_dir=mock_project_env["config_dir"],
        project_root=mock_project_env["root"]
    )

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.return_value = {
        "id": "backup_file_id_999",
        "name": "internships_backup.db",
        "size": 12345
    }

    with patch.object(syncer, "get_service", return_value=mock_service):
        res = syncer.backup_database(db_path=mock_project_env["db_file"])
        assert res["action"] == "created"
        assert res["file_id"] == "backup_file_id_999"
        assert Path(res["local_snapshot"]).exists()

def test_schedule_script_and_plist_exist():
    project_root = Path(__file__).resolve().parent.parent
    script_path = project_root / "scripts" / "daily_digest.sh"
    assert script_path.exists(), "scripts/daily_digest.sh should exist"
    assert os.access(script_path, os.X_OK), "scripts/daily_digest.sh should be executable"

    launchagent_path = Path.home() / "Library" / "LaunchAgents" / "com.antigravity.internship.daily.plist"
    assert launchagent_path.exists(), "LaunchAgent plist should exist"
    content = launchagent_path.read_text()
    assert "com.antigravity.internship.daily" in content
    assert "daily_digest.sh" in content
    assert "<key>Hour</key>" in content
    assert "<integer>4</integer>" in content
