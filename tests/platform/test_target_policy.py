"""P2-E2 URI Policy 测试：canonicalize/classify/AuthorizationService/TargetPolicy
（02 §7.5，05 §11.5，14 号计划 §97.2）。

验收映射：
- AC③：URI 分类覆盖 user/resources/agent/skills 与 internal 根，internal
  默认拒绝（02 §7.5，05 §11.5）；
- AC④：canonical URI 一致性校验失败即拒；客户端提交的 visibility/ID 不能
  单独作为授权依据；
- 05 §11.5：动作→Permission 映射、默认目标规则（未显式目标强制 Actor 私有根）。
"""

from __future__ import annotations

import uuid

import pytest

from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.auth.uri_policy import (
    AuthorizationService,
    OVMapper,
    canonicalize_uri,
    classify_target,
)
from openviking.server.platform.errors import (
    AccessDeniedError,
    CanonicalUriMismatchError,
    InvalidTargetError,
)
from openviking.server.platform.iam.permissions import (
    ACCOUNT_ADMIN_PERMISSIONS,
    PLATFORM_PERMISSIONS,
    USER_PERMISSIONS,
)
from openviking.server.platform.target_policy import TargetPolicy


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _principal(
    *,
    role_codes=("user",),
    account_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    ov_user_id: str | None = None,
    ov_account_id: str | None = None,
    permissions: frozenset[str] = USER_PERMISSIONS,
) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user_id or _uid(100),
        actor_account_id=account_id,
        actor_ov_user_id=ov_user_id or ("ov_user_alice" if account_id else None),
        actor_ov_account_id=ov_account_id or ("ov_account_acme" if account_id else None),
        user_status="active",
        authentication_method="session",
        role_codes=tuple(role_codes),
        permissions=permissions,
    )


def _mapper() -> OVMapper:
    async def resolve_user(user_id: uuid.UUID):
        if user_id == _uid(1):
            return ("ov_account_acme", "ov_user_alice")
        if user_id == _uid(5):
            return ("ov_account_acme", "ov_user_bob")
        return None

    async def resolve_account(account_id: uuid.UUID):
        if account_id == _uid(2):
            return "ov_account_acme"
        if account_id == _uid(3):
            return "ov_account_beta"
        return None

    return OVMapper(resolve_user=resolve_user, resolve_account=resolve_account)


def _auth(permissions=frozenset()):
    return AuthorizationService(TargetPolicy(), _mapper())


# ── AC③：canonicalize + classify ──


async def test_canonicalize_folds_slashes_and_trailing() -> None:
    assert canonicalize_uri("viking://user/ov_user_alice/resources/foo.md/") == (
        "viking://user/ov_user_alice/resources/foo.md"
    )
    assert canonicalize_uri("viking://user/ov_user_alice//resources///foo.md") == (
        "viking://user/ov_user_alice/resources/foo.md"
    )


async def test_canonicalize_rejects_non_viking_uri() -> None:
    for bad in ("http://x/y", "viking://", "file:///etc/passwd", ""):
        with pytest.raises(InvalidTargetError):
            canonicalize_uri(bad)


async def test_classify_user_private_roots() -> None:
    assert classify_target("viking://user/ov_user_alice/resources/x.md") == ("user_private", "ov_user_alice")
    assert classify_target("viking://user/ov_user_alice/skills/s.md") == ("user_private", "ov_user_alice")


async def test_classify_account_shared_roots() -> None:
    assert classify_target("viking://resources/foo.md") == ("account_shared", None)
    assert classify_target("viking://agent/skills/s.md") == ("account_shared", None)


async def test_classify_internal_roots_denied_by_default() -> None:
    """AC③：internal 根默认拒绝（agent/endpoints/tools/payments/未知根）。"""
    for uri in (
        "viking://agent/endpoints/x",
        "viking://agent/other/x",
        "viking://tools/x",
        "viking://payments/x",
        "viking://internal/fs/root",
        "viking://someunknownroot/x",
    ):
        assert classify_target(uri) == ("internal", None)
        with pytest.raises(AccessDeniedError) as excinfo:
            await _auth().authorize(
                _principal(account_id=_uid(2), user_id=_uid(1)),
                action="read",
                uri=uri,
                object_type="resource",
            )
        assert excinfo.value.reason == "INTERNAL_TARGET_DENIED"


# ── AC④：统一授权门一致性 ──


async def test_authorize_rejects_forged_other_user_root() -> None:
    """客户端伪造他人 user 私有根 → CanonicalUriMismatchError（AC④）。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    with pytest.raises(CanonicalUriMismatchError):
        await _auth().authorize(
            alice,
            action="read",
            uri="viking://user/ov_user_bob/resources/x.md",  # bob 的根
            object_type="resource",
        )


async def test_authorize_rejects_uri_user_mismatch_for_admin_subject() -> None:
    """管理接口指定 Subject 时 URI 用户必须等于服务端映射（AC④）。"""
    admin = _principal(
        account_id=_uid(2),
        user_id=_uid(9),
        role_codes=("account_admin",),
        permissions=frozenset(ACCOUNT_ADMIN_PERMISSIONS),
    )
    with pytest.raises(CanonicalUriMismatchError):
        await _auth().authorize(
            admin,
            action="read",
            uri="viking://user/ov_user_alice/resources/x.md",
            object_type="resource",
            subject_account_id=_uid(2),
            subject_user_id=_uid(5),  # bob，但 URI 指向 alice 根
        )


async def test_authorize_user_private_self_ok() -> None:
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = await _auth().authorize(
        alice, action="read", uri="viking://user/ov_user_alice/resources/x.md", object_type="resource"
    )
    assert access.visibility == "user_private"
    assert access.subject_user_id == _uid(1)
    assert access.subject_ov_user_id == "ov_user_alice"
    assert access.canonical_ov_uri == "viking://user/ov_user_alice/resources/x.md"


async def test_authorize_account_shared_read_for_plain_user() -> None:
    """普通 User 可读取 Account 共享 Resource（05 §11.5：`.read.account`）。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = await _auth().authorize(
        alice, action="read", uri="viking://resources/foo.md", object_type="resource"
    )
    assert access.visibility == "account_shared"
    assert access.subject_user_id is None
    assert access.subject_ov_account_id == "ov_account_acme"


async def test_plain_user_shared_write_denied() -> None:
    """普通 User 写 Account 共享区 → 403（05 §11.5：显式共享目标触发写权限检查）。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    with pytest.raises(AccessDeniedError) as excinfo:
        await _auth().authorize(
            alice, action="write", uri="viking://resources/foo.md", object_type="resource"
        )
    assert excinfo.value.reason == "PERMISSION_NOT_GRANTED"


async def test_account_admin_shared_write_ok() -> None:
    admin = _principal(
        account_id=_uid(2),
        user_id=_uid(9),
        role_codes=("account_admin",),
        permissions=frozenset(ACCOUNT_ADMIN_PERMISSIONS),
    )
    access = await _auth().authorize(
        admin, action="write", uri="viking://resources/foo.md", object_type="resource"
    )
    assert access.subject_user_id is None


async def test_psa_shared_write_uses_platform_permission() -> None:
    psa = _principal(
        role_codes=("platform_super_admin",),
        account_id=None,
        user_id=_uid(900),
        permissions=frozenset(PLATFORM_PERMISSIONS),
    )
    access = await _auth().authorize(
        psa,
        action="write",
        uri="viking://resources/foo.md",
        object_type="resource",
        subject_account_id=_uid(3),  # beta Account（跨 Account 管理浏览）
    )
    assert access.subject_account_id == _uid(3)
    assert access.subject_ov_account_id == "ov_account_beta"


async def test_psa_skill_write_denied_even_with_platform_permissions() -> None:
    """PSA 对 Skill 始终只读（05 §12.6 注 / §11.5：Platform 只有 .read.platform）。"""
    psa = _principal(
        role_codes=("platform_super_admin",),
        account_id=None,
        user_id=_uid(900),
        permissions=frozenset(PLATFORM_PERMISSIONS),
    )
    with pytest.raises(AccessDeniedError) as excinfo:
        await _auth().authorize(
            psa,
            action="write",
            uri="viking://agent/skills/s.md",
            object_type="skill",
            subject_account_id=_uid(3),
        )
    assert excinfo.value.reason == "PERMISSION_NOT_GRANTED"


async def test_skill_publish_only_account_admin() -> None:
    """发布其他 User 的私有 Skill：仅 Account Admin `skill.user_private.publish.account`。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    with pytest.raises(AccessDeniedError):
        await _auth().authorize(
            alice,
            action="publish",
            uri="viking://user/ov_user_bob/skills/s.md",
            object_type="skill",
            subject_account_id=_uid(2),
            subject_user_id=_uid(5),
        )
    admin = _principal(
        account_id=_uid(2),
        user_id=_uid(9),
        role_codes=("account_admin",),
        permissions=frozenset(ACCOUNT_ADMIN_PERMISSIONS),
    )
    access = await _auth().authorize(
        admin,
        action="publish",
        uri="viking://user/ov_user_bob/skills/s.md",
        object_type="skill",
        subject_account_id=_uid(2),
        subject_user_id=_uid(5),
    )
    assert access.subject_user_id == _uid(5)


async def test_cross_account_subject_denied_for_admin() -> None:
    """Account Admin 指定跨 Account Subject → 数据范围拒绝（02 §7.2）。"""
    admin = _principal(
        account_id=_uid(2),
        user_id=_uid(9),
        role_codes=("account_admin",),
        permissions=frozenset(ACCOUNT_ADMIN_PERMISSIONS),
    )
    with pytest.raises(AccessDeniedError) as excinfo:
        await _auth().authorize(
            admin,
            action="read",
            uri="viking://user/ov_user_alice/resources/x.md",
            object_type="resource",
            subject_account_id=_uid(3),  # beta
            subject_user_id=_uid(1),
        )
    assert excinfo.value.reason == "CROSS_ACCOUNT_ACCESS"


# ── 05 §11.5：默认目标规则与 Permission 映射 ──


async def test_default_target_rule_forces_actor_private_root() -> None:
    """未显式指定目标时服务端强制 Actor 私有根（05 §11.5 默认目标规则）。"""
    policy = TargetPolicy()
    assert policy.default_target_uri("ov_user_alice", object_type="resource") == (
        "viking://user/ov_user_alice/resources/"
    )
    assert policy.default_target_uri("ov_user_alice", object_type="skill") == (
        "viking://user/ov_user_alice/skills/"
    )


async def test_target_policy_permission_mapping() -> None:
    policy = TargetPolicy()
    cases = {
        ("resource", "user_private", "read", "self"): "resource.user_private.read.self",
        ("resource", "user_private", "write", "self"): "resource.user_private.write.self",
        ("resource", "user_private", "delete", "self"): "resource.user_private.delete.self",
        ("resource", "account_shared", "read", "account"): "resource.account_shared.read.account",
        ("resource", "account_shared", "write", "platform"): "resource.account_shared.write.platform",
        ("resource", "account_shared", "delete", "platform"): "resource.account_shared.delete.platform",
        ("skill", "user_private", "manage", "self"): "skill.user_private.manage.self",
        ("skill", "user_private", "publish", "account"): "skill.user_private.publish.account",
        ("skill", "account_shared", "use", "account"): "skill.account_shared.use.account",
        ("skill", "account_shared", "manage", "account"): "skill.account_shared.manage.account",
        ("resource", "account_shared", "delete", "account"): "resource.account_shared.delete.account",
    }
    for (obj, vis, action, scope), expected in cases.items():
        assert (
            policy.permission_for(object_type=obj, visibility=vis, action=action, scope=scope)
            == expected
        )
    # 未映射组合默认拒绝
    assert policy.permission_for(object_type=None, visibility="user_private", action="read", scope="self") is None
    assert policy.permission_for(object_type="resource", visibility="internal", action="read", scope="self") is None
