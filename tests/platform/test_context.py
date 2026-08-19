"""P2-E2 身份上下文测试：DataAccessContext/authorize_data_access/to_ov_context
/to_ov_account_context（02 §7.2/§7.3，14 号计划 §97.2）。

验收映射：
- AC②：to_ov_context 仅 user_private 且 subject 非空；to_ov_account_context
  仅 account_shared 且 subject_user 为空；跨 Account 执行载体固定
  platform-gateway（02 §7.3）；
- AC④：客户端提交的 visibility/ID 不能单独作为授权依据；canonical URI
  一致性校验失败即拒（02 §7.2）；
- 平台 rank 与 OpenViking rank 隔离：转换恒为 Role.USER，不提升执行上下文。
"""

from __future__ import annotations

import uuid

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.platform.auth.access import DataAccessContext, authorize_data_access
from openviking.server.platform.auth.ov_context import (
    PLATFORM_GATEWAY_USER,
    to_ov_account_context,
    to_ov_context,
)
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.errors import AccessDeniedError, CanonicalUriMismatchError
from openviking_cli.session.user_id import UserIdentifier


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _principal(
    *,
    role_codes=("user",),
    account_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    ov_user_id: str | None = None,
    ov_account_id: str | None = None,
) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user_id or _uid(100),
        actor_account_id=account_id,
        actor_ov_user_id=ov_user_id or ("ov_user_alice" if account_id else None),
        actor_ov_account_id=ov_account_id or ("ov_account_acme" if account_id else None),
        user_status="active",
        authentication_method="session",
        role_codes=tuple(role_codes),
    )


def _access(
    *,
    actor,
    subject_account_id: uuid.UUID,
    subject_user_id: uuid.UUID | None,
    visibility: str,
    canonical_ov_uri: str,
    subject_ov_account_id: str = "ov_account_acme",
    subject_ov_user_id: str | None = "ov_user_alice",
    action: str = "read",
) -> DataAccessContext:
    return DataAccessContext(
        actor_user_id=actor.actor_user_id,
        actor_account_id=actor.actor_account_id,
        subject_account_id=subject_account_id,
        subject_user_id=subject_user_id,
        subject_ov_account_id=subject_ov_account_id,
        subject_ov_user_id=subject_ov_user_id,
        visibility=visibility,  # type: ignore[arg-type]
        canonical_ov_uri=canonical_ov_uri,
        action=action,
        request_id="req-1",
    )


# ── AC②：to_ov_context / to_ov_account_context ──


async def test_to_ov_context_user_private_subject_minimal_role() -> None:
    alice = _principal(account_id=_uid(2), user_id=_uid(1), ov_user_id="ov_user_alice")
    access = _access(
        actor=alice,
        subject_account_id=_uid(2),
        subject_user_id=_uid(1),
        visibility="user_private",
        canonical_ov_uri="viking://user/ov_user_alice/resources/foo.md",
    )
    rc = to_ov_context(alice, access)
    assert isinstance(rc, RequestContext)
    assert rc.user == UserIdentifier("ov_account_acme", "ov_user_alice")
    assert rc.role == Role.USER
    assert rc.actor_peer_id is None


async def test_to_ov_context_rejects_account_shared() -> None:
    """AC②：to_ov_context 仅 user_private（assert 防护 + 文档约束）。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(2),
        subject_user_id=None,
        visibility="account_shared",
        canonical_ov_uri="viking://resources/foo.md",
        subject_ov_user_id=None,
    )
    with pytest.raises(AssertionError):
        to_ov_context(alice, access)


async def test_to_ov_account_context_requires_empty_subject_user() -> None:
    """AC②：to_ov_account_context 仅 account_shared 且 subject_user 为空。

    account_shared 携带 Subject User → canonical URI 一致性校验先拒（AC④），
    不虚构"共享资源所属用户"（02 §7.2）。
    """
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(2),
        subject_user_id=_uid(1),
        visibility="account_shared",
        canonical_ov_uri="viking://resources/foo.md",
        subject_ov_user_id=None,
    )
    with pytest.raises(CanonicalUriMismatchError):
        to_ov_account_context(alice, access)


async def test_to_ov_account_context_same_account_uses_own_ov_user() -> None:
    """同 Account 的 User/Account Admin 使用自己的 OpenViking User ID（02 §7.3）。"""
    admin = _principal(
        account_id=_uid(2), user_id=_uid(9), role_codes=("account_admin",), ov_user_id="ov_user_admin"
    )
    access = _access(
        actor=admin,
        subject_account_id=_uid(2),
        subject_user_id=None,
        visibility="account_shared",
        canonical_ov_uri="viking://resources/shared.md",
        subject_ov_user_id=None,
    )
    rc = to_ov_account_context(admin, access)
    assert rc.user == UserIdentifier("ov_account_acme", "ov_user_admin")
    assert rc.role == Role.USER


async def test_to_ov_account_context_psa_cross_account_uses_platform_gateway() -> None:
    """AC②：PSA 跨 Account 执行载体固定 platform-gateway（02 §7.3，非 Service Account）。"""
    psa = _principal(role_codes=("platform_super_admin",), account_id=None, user_id=_uid(900))
    access = _access(
        actor=psa,
        subject_account_id=_uid(2),
        subject_user_id=None,
        visibility="account_shared",
        canonical_ov_uri="viking://resources/shared.md",
        subject_ov_user_id=None,
    )
    rc = to_ov_account_context(psa, access)
    assert rc.user == UserIdentifier("ov_account_acme", PLATFORM_GATEWAY_USER)
    assert rc.role == Role.USER


async def test_conversion_never_raises_role_above_user() -> None:
    """平台 rank 与 OpenViking rank 隔离：Account Admin/PSA 也不提升为 Root/Admin。"""
    psa = _principal(role_codes=("platform_super_admin",), account_id=None, user_id=_uid(900))
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    rc_user = to_ov_context(
        alice,
        _access(
            actor=alice,
            subject_account_id=_uid(2),
            subject_user_id=_uid(1),
            visibility="user_private",
            canonical_ov_uri="viking://user/ov_user_alice/x.md",
        ),
    )
    rc_shared = to_ov_account_context(
        psa,
        _access(
            actor=psa,
            subject_account_id=_uid(2),
            subject_user_id=None,
            visibility="account_shared",
            canonical_ov_uri="viking://resources/x.md",
            subject_ov_user_id=None,
        ),
    )
    assert rc_user.role == Role.USER and rc_shared.role == Role.USER


# ── AC④：canonical URI 一致性（客户端提交不能单独作为授权依据）──


async def test_authorize_rejects_user_private_uri_of_another_user() -> None:
    """客户端把 user_private 指到他人 ov user 根 → 拒（AC④）。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(2),
        subject_user_id=_uid(1),
        visibility="user_private",
        canonical_ov_uri="viking://user/ov_user_mallory/x.md",  # 伪造他人根
    )
    with pytest.raises(CanonicalUriMismatchError):
        authorize_data_access(alice, access)


async def test_authorize_rejects_account_shared_uri_under_user_root() -> None:
    """account_shared 声明但 URI 落在 user 私有根 → 拒（AC④）。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(2),
        subject_user_id=None,
        visibility="account_shared",
        canonical_ov_uri="viking://user/ov_user_alice/x.md",
        subject_ov_user_id=None,
    )
    with pytest.raises(CanonicalUriMismatchError):
        authorize_data_access(alice, access)


async def test_authorize_rejects_user_private_without_subject_user() -> None:
    """user_private 但 Subject User 为空（虚构共享对象归属者）→ 拒（02 §7.2）。"""
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(2),
        subject_user_id=None,
        visibility="user_private",
        canonical_ov_uri="viking://user/ov_user_alice/x.md",
        subject_ov_user_id=None,
    )
    with pytest.raises(CanonicalUriMismatchError):
        authorize_data_access(alice, access)


# ── 数据范围（02 §7.2）──


async def test_cross_account_user_private_denied() -> None:
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(3),  # 另一 Account
        subject_user_id=_uid(1),
        visibility="user_private",
        canonical_ov_uri="viking://user/ov_user_alice/x.md",
        subject_ov_account_id="ov_account_beta",
    )
    with pytest.raises(AccessDeniedError):
        authorize_data_access(alice, access)


async def test_account_admin_can_read_any_subject_in_account() -> None:
    """Account Admin 可读取本 Account 内任意 Subject（02 §7.2；写/删仍须独立 Permission）。"""
    admin = _principal(
        account_id=_uid(2), user_id=_uid(9), role_codes=("account_admin",), ov_user_id="ov_user_admin"
    )
    access = _access(
        actor=admin,
        subject_account_id=_uid(2),
        subject_user_id=_uid(1),
        visibility="user_private",
        canonical_ov_uri="viking://user/ov_user_alice/x.md",
    )
    authorize_data_access(admin, access)  # 不抛


async def test_psa_reads_any_subject_any_account() -> None:
    psa = _principal(role_codes=("platform_super_admin",), account_id=None, user_id=_uid(900))
    access = _access(
        actor=psa,
        subject_account_id=_uid(2),
        subject_user_id=_uid(1),
        visibility="user_private",
        canonical_ov_uri="viking://user/ov_user_alice/x.md",
        subject_ov_account_id="ov_account_acme",
    )
    authorize_data_access(psa, access)


async def test_plain_user_cannot_access_other_users_private() -> None:
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(2),
        subject_user_id=_uid(5),  # 同 Account 另一 User（非 Admin）
        visibility="user_private",
        canonical_ov_uri="viking://user/ov_user_bob/x.md",
        subject_ov_user_id="ov_user_bob",
    )
    with pytest.raises(AccessDeniedError):
        authorize_data_access(alice, access)


async def test_cross_account_shared_denied_for_account_member() -> None:
    alice = _principal(account_id=_uid(2), user_id=_uid(1))
    access = _access(
        actor=alice,
        subject_account_id=_uid(3),
        subject_user_id=None,
        visibility="account_shared",
        canonical_ov_uri="viking://resources/x.md",
        subject_ov_user_id=None,
        subject_ov_account_id="ov_account_beta",
    )
    with pytest.raises(AccessDeniedError):
        authorize_data_access(alice, access)
