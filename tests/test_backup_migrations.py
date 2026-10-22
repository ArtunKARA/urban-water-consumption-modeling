import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import reading
from urban_water.backup import create_backup, restore_backup
from urban_water.datasets import freeze, verify_dataset
from urban_water.db import PROJECT_ROOT, connect, migrate
from urban_water.ingestion import accept, work_once
from urban_water.processing import summarize

pytestmark = pytest.mark.postgres


def test_migration_upgrade_preserves_data_and_rejects_edits(settings, source_file, tmp_path):
    accept(settings, source_file([reading()]))
    assert work_once(settings)["state"] == "succeeded"
    directory = tmp_path / "migrations"
    shutil.copytree(PROJECT_ROOT / "migrations", directory)
    (directory / "004_test_upgrade.sql").write_text(
        "ALTER TABLE uw.study ADD COLUMN test_upgrade integer NOT NULL DEFAULT 7;"
    )
    assert migrate(settings, directory) == ["004_test_upgrade.sql"]
    assert migrate(settings, directory) == []
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM measurement").fetchone()["n"] == 1
        assert conn.execute("SELECT test_upgrade FROM study").fetchone()["test_upgrade"] == 7
    (directory / "004_test_upgrade.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="applied migration changed"):
        migrate(settings, directory)


def test_backup_restore_files_counts_results_and_frozen_dataset(
    settings,
    restore_settings,
    source_file,
    tmp_path,
):
    accept(settings, source_file([reading(), reading(event="second", value="3")]))
    assert work_once(settings)["state"] == "succeeded"
    frozen = freeze(settings)
    expected = summarize(settings)
    backup = tmp_path / "backup"
    report = create_backup(settings, backup)
    assert report["counts"]["measurement"] == 2
    result = restore_backup(restore_settings, backup)
    assert result["restored"]
    assert summarize(restore_settings) == expected
    assert verify_dataset(restore_settings, frozen["dataset_id"])["verified"]
    assert migrate(restore_settings) == []
    with pytest.raises(ValueError, match="storage root must be empty"):
        restore_backup(restore_settings, backup)


def test_backup_corruption_rejected_before_any_database_write(
    settings,
    restore_settings,
    source_file,
    tmp_path,
):
    accept(settings, source_file([reading()]))
    work_once(settings)
    directory = tmp_path / "backup"
    create_backup(settings, directory)
    with (directory / "database.dump").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        restore_backup(restore_settings, directory)
    with connect(restore_settings) as conn:
        assert conn.execute("SELECT to_regclass('uw.measurement') AS t").fetchone()["t"] is None


def test_restore_refuses_active_database(settings, source_file, tmp_path):
    accept(settings, source_file([reading()]))
    work_once(settings)
    directory = tmp_path / "backup"
    create_backup(settings, directory)
    target = replace(settings, storage_root=tmp_path / "new-storage")
    with pytest.raises(ValueError, match="different, empty"):
        restore_backup(target, directory)


def test_backup_cannot_be_written_inside_active_storage(settings):
    with pytest.raises(ValueError, match="outside active storage"):
        create_backup(settings, Path(settings.storage_root) / "backup")
