"""P5-E3 备份/恢复一致性校验测试（14 号计划 §99.3，06 §14.4/§15.4）。

复用 deploy/product/scripts/drill_lib.py（演练库）：prepare/snapshot/
fault-inject/verify 为真实演练与自动化测试共用的同一套逻辑（"能用真实演练
脚本验证的优先真实演练，脚本化的走自动化测试"）。

- test_drill_verify_passes_without_fault / _detects_fault：校验逻辑正/负向；
- test_restore_audit_required：恢复审计（backup.restore 进 iam_audit_events，AC⑤）；
- test_encrypted_backup_roundtrip：backup→verify→list 冒烟（需 gpg + pg 工具，
  缺失时 skip；恢复路径由真实演练 p5-e3-drill-record.md 覆盖）。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import hash_password
from openviking.server.platform.iam import PostgresIamRepository, RbacService

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_drill_lib():
    spec = importlib.util.spec_from_file_location(
        "drill_lib", _REPO_ROOT / "deploy/product/scripts/drill_lib.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drill_lib = _load_drill_lib()

PSA_EMAIL = "psa@platform.local"


async def _create_psa_only(session: AsyncSession) -> None:
    """只建 PSA（测试库被逐测试清空；Account/User 由演练库 prepare 自建）。"""
    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    await rbac.seed_catalog(session)
    await repo.create_user(
        session,
        account_id=None,
        ov_user_id=None,
        username="psa",
        email=PSA_EMAIL,
        display_name="PSA Drill",
        password_hash=hash_password("Drill-PSA-2026-Dev!"),
        status="active",
    )
    await session.commit()
    psa = await repo.get_user_by_normalized_email(session, PSA_EMAIL)
    assert psa is not None
    await rbac.assign_platform_super_admin(session, target_user_id=psa.id)
    await session.commit()


async def _seed_restore_audit(session: AsyncSession) -> None:
    """写一条 backup.restore 审计（模拟真实恢复脚本写入，06 §14.4）。"""
    await session.execute(
        text(
            "INSERT INTO iam_audit_events (id, actor_type, actor_system_component, action, "
            "target_type, target_id, scope, result) VALUES "
            "(gen_random_uuid(), 'system', 'backup-ops', 'backup.restore', 'postgres', "
            "'ov_platform_test', 'platform', 'completed')"
        )
    )
    await session.commit()


async def test_drill_verify_passes_without_fault(
    session: AsyncSession, test_database: str
) -> None:
    """演练库校验逻辑正向：prepare→(模拟恢复审计)→verify 全绿（基线自检）。"""
    await _create_psa_only(session)
    data = await drill_lib.prepare_business_data(test_database, psa_email=PSA_EMAIL)
    before = await drill_lib.snapshot_counts(test_database)
    await _seed_restore_audit(session)
    report = await drill_lib.verify_restore(
        test_database,
        before=before,
        session_raw=data["session_raw"],
        alice_key=data["alice_key"],
        retry_failed_account_id=data["failed_account_id"],
        psa_email=PSA_EMAIL,
    )
    fails = [f"{c['name']}: {c['detail']}" for c in report["checks"] if not c["ok"]]
    assert report["ok"], fails
    names = {c["name"]: c["ok"] for c in report["checks"]}
    for required in (
        "login_password",
        "session_resolve",
        "api_key_resolve",
        "audit_continuity",
        "restore_audited",
        "no_dup_content_refs",
        "no_dup_session_refs",
        "no_dup_outbox",
        "retry_failed_account",
        "retry_idempotent_no_dup",
    ):
        assert names.get(required) is True, f"check {required}: {report['checks']}"


async def test_drill_verify_detects_fault(
    session: AsyncSession, test_database: str
) -> None:
    """演练库校验逻辑负向：故障注入后 verify 必须报失败（校验真实有效）。"""
    await _create_psa_only(session)
    data = await drill_lib.prepare_business_data(test_database, psa_email=PSA_EMAIL)
    before = await drill_lib.snapshot_counts(test_database)
    await _seed_restore_audit(session)
    await drill_lib.fault_inject(test_database)
    report = await drill_lib.verify_restore(
        test_database,
        before=before,
        session_raw=data["session_raw"],
        alice_key=data["alice_key"],
        retry_failed_account_id=data["failed_account_id"],
        psa_email=PSA_EMAIL,
    )
    fails = [f"{c['name']}: {c['detail']}" for c in report["checks"] if not c["ok"]]
    assert report["ok"] is False, f"故障注入后校验必须失败（校验真实性）: {fails}"
    failed_names = [c["name"] for c in report["checks"] if not c["ok"]]
    assert "login_password" in failed_names or "count_iam_users" in failed_names


async def test_restore_audit_required(
    session: AsyncSession, test_database: str
) -> None:
    """恢复操作审计强制（AC⑤）：无 backup.restore 记录时 restore_audited 必须失败，
    写入后通过（演练脚本经 backup-encrypt.sh audit_db 写 iam_audit_events）。"""
    await _create_psa_only(session)
    data = await drill_lib.prepare_business_data(test_database, psa_email=PSA_EMAIL)
    before = await drill_lib.snapshot_counts(test_database)
    report = await drill_lib.verify_restore(
        test_database,
        before=before,
        session_raw=data["session_raw"],
        alice_key=data["alice_key"],
        psa_email=PSA_EMAIL,
    )
    fails = [f"{c['name']}: {c['detail']}" for c in report["checks"] if not c["ok"]]
    assert not report["ok"], f"缺少恢复审计时校验必须失败: {fails}"
    assert any(
        c["name"] == "restore_audited" and not c["ok"] for c in report["checks"]
    )
    await _seed_restore_audit(session)
    report = await drill_lib.verify_restore(
        test_database,
        before=before,
        session_raw=data["session_raw"],
        alice_key=data["alice_key"],
        psa_email=PSA_EMAIL,
    )
    fails = [f"{c['name']}: {c['detail']}" for c in report["checks"] if not c["ok"]]
    assert report["ok"], fails


def _pg_tools_available() -> str | None:
    """返回可用的 PG 工具调用前缀（'' = 本地二进制），不可用返回 None。"""
    if shutil.which("gpg") is None:
        return None
    if shutil.which("pg_dump") is not None and shutil.which("pg_restore") is not None:
        return ""
    container = os.environ.get("OV_PLATFORM_TEST_PG_CONTAINER", "")
    if container and shutil.which("docker"):
        try:
            subprocess.run(
                ["docker", "exec", container, "which", "pg_dump"],
                check=True,
                capture_output=True,
            )
            return "docker exec -i " + container
        except subprocess.CalledProcessError:
            return None
    return None


@pytest.mark.skipif(
    _pg_tools_available() is None,
    reason="需要 gpg + pg_dump/pg_restore（本地或 OV_PLATFORM_TEST_PG_CONTAINER 容器）",
)
def test_encrypted_backup_roundtrip(test_database: str, tmp_path) -> None:
    """加密备份脚本 round-trip：backup→verify→list（GPG AES256 解密可读，AC⑤）。

    只读操作（不恢复进测试库，避免破坏共享测试库；恢复路径由真实演练覆盖）。
    """
    pg_tools = _pg_tools_available()
    admin_url = os.environ.get(
        "OV_PLATFORM_TEST_ADMIN_URL",
        "postgresql://ov_platform:ov_platform_dev@127.0.0.1:55432/postgres",
    )
    user = admin_url.split("//")[1].split(":")[0]
    password = admin_url.split("//")[1].split(":")[1].split("@")[0]
    host = admin_url.split("@")[1].split("/")[0].split(":")[0]
    port = admin_url.split("@")[1].split("/")[0].split(":")[1]
    db = test_database.split("/")[-1]
    tools_host = "host.docker.internal" if pg_tools.startswith("docker") else host
    dsn = f"postgresql://{user}:{password}@{tools_host}:{port}/{db}"

    script = _REPO_ROOT / "deploy/product/scripts/backup-encrypt.sh"
    env = {
        **os.environ,
        "OV_PLATFORM_DATABASE_URL": dsn,
        "OV_PLATFORM_BACKUP_PASSPHRASE": "pytest-backup-pass-2026",
        "OV_PLATFORM_BACKUP_AUDIT_LOG": str(tmp_path / "audit.log"),
        "OV_PLATFORM_PG_TOOLS": pg_tools,
    }
    subprocess.run(["bash", str(script), "backup", str(tmp_path / "bk")],
                   check=True, env=env, capture_output=True)
    enc = next((tmp_path / "bk").glob("ov_platform_*.dump.gz.gpg"))
    subprocess.run(["bash", str(script), "verify", str(enc)],
                   check=True, env=env, capture_output=True)
    listed = subprocess.run(["bash", str(script), "list", str(enc)],
                            check=True, env=env, capture_output=True, text=True)
    assert "TABLE DATA" in listed.stdout
    assert (tmp_path / "audit.log").read_text().count("kind=backup") >= 1
    assert enc.stat().st_mode & 0o777 == 0o600
