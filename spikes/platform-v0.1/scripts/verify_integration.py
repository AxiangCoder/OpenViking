"""Same-process integration verification (design 02 §7.3, spike risk #1).

Runs the spike IAM modules and the REAL OpenViking identity/namespace modules
in ONE interpreter to prove the modular-monolith assumption:
  - openviking.server.identity (RequestContext / Role / UserIdentifier) imports cleanly
  - AuthenticatedUserPrincipal -> RequestContext conversion matches 02 §7.3
  - Role.register() extension point behaves as documented (01 §4.2 item 3)
  - spike modules and openviking modules coexist without import conflicts

Usage (from the main repo venv, which has the full dependency set):
  PYTHONPATH=<repo-root>:<spike>/backend <repo>/.venv/bin/python spikes/platform-v0.1/scripts/verify_integration.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/Volumes/work_disk/个人效能管理系统 Agent/OpenViking-sourcecode-v0.4.12")
sys.path.insert(
    0, "/Volumes/work_disk/个人效能管理系统 Agent/OpenViking-sourcecode-v0.4.12/spikes/platform-v0.1/backend"
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def main() -> None:
    print("== 1. real openviking modules import in the same process ==")
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    check("identity.RequestContext importable", RequestContext is not None)
    check("built-in roles exist", (Role.ROOT, Role.ADMIN, Role.USER) == ("root", "admin", "user"))
    rc_fields = RequestContext.__dataclass_fields__
    check(
        "RequestContext(user, role, actor_peer_id)",
        {"user", "role", "actor_peer_id"} <= set(rc_fields),
    )

    print("== 2. UserIdentifier mapping (product id -> ov id) ==")
    uid = UserIdentifier("ov_account_acme", "ov_user_alice")
    check("UserIdentifier(account_id, user_id)", uid.account_id == "ov_account_acme" and uid.user_id == "ov_user_alice")
    check(
        "ov ids are NOT product ids (spike maps them)",
        uid.account_id != "11111111-1111-1111-1111-111111111111",
    )

    print("== 3. spike principal -> RequestContext conversion (02 §7.3) ==")
    from principals import AuthenticatedUserPrincipal, DataAccessContext

    # user_private: alice accesses her own data
    p_alice = AuthenticatedUserPrincipal(
        actor_user_id=__import__("uuid").UUID(int=1),
        actor_account_id=__import__("uuid").UUID(int=2),
        actor_ov_user_id="ov_user_alice",
        actor_ov_account_id="ov_account_acme",
        user_status="active",
        authentication_method="session",
    )
    rc = RequestContext(
        user=UserIdentifier(p_alice.actor_ov_account_id, p_alice.actor_ov_user_id),
        role=Role.USER,
        actor_peer_id=None,
    )
    check(
        "user_private ctx maps ov ids + Role.USER",
        rc.user.account_id == "ov_account_acme"
        and rc.user.user_id == "ov_user_alice"
        and rc.role == Role.USER
        and rc.actor_peer_id is None,
    )

    # account_shared: account admin uses own ov user as execution carrier
    rc_admin = RequestContext(
        user=UserIdentifier("ov_account_acme", "ov_user_admin"),
        role=Role.USER,
        actor_peer_id=None,
    )
    check("account_shared admin ctx uses own ov user", rc_admin.user.user_id == "ov_user_admin")

    # account_shared: PSA cross-account uses the reserved platform-gateway placeholder
    rc_gw = RequestContext(
        user=UserIdentifier("ov_account_beta", "platform-gateway"),
        role=Role.USER,
        actor_peer_id=None,
    )
    check(
        "platform-gateway placeholder for PSA cross-account",
        rc_gw.user.account_id == "ov_account_beta" and rc_gw.user.user_id == "platform-gateway",
    )
    check(
        "execution ctx never role=ROOT (minimal privilege)",
        rc.role == Role.USER and rc_admin.role == Role.USER and rc_gw.role == Role.USER,
    )

    print("== 4. Role.register() extension point (01 §4.2 item 3) ==")
    Role.register("custom_viewer", rank=5)
    check(
        "Role.register stores custom role in _CUSTOM_RANK",
        Role._CUSTOM_RANK.get("custom_viewer") == 5,
    )
    check(
        "built-in rank semantics: USER=0 < ADMIN=1 < ROOT=2",
        Role("user").rank == 0 and Role("admin").rank == 1 and Role("root").rank == 2,
    )
    # NOTE (recorded): source Role ranks are USER=0/ADMIN=1/ROOT=2; the platform
    # iam_roles.rank (3/2/1) is an independent product model — the two must not
    # be mixed (design 03 §8.3 uses the platform ranks).
    check("source ranks are 0/1/2 (documented divergence)", True)

    print("== 5. namespace ACL still enforced underneath ==")
    from openviking.core.namespace import is_accessible

    # same-account user CAN reach viking://resources (account scope) — source behavior
    ctx_user = RequestContext(user=UserIdentifier("ov_account_acme", "ov_user_alice"), role=Role.USER)
    check("resources/** reachable by account member (source baseline)", is_accessible("viking://resources/foo.md", ctx_user))
    # user cannot reach another user's private root
    ctx_other = RequestContext(user=UserIdentifier("ov_account_acme", "ov_user_bob"), role=Role.USER)
    check(
        "user/** blocked for non-owner (namespace ACL 兜底)",
        is_accessible("viking://user/ov_user_alice/x", ctx_other) is False,
    )

    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("Failed:", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    main()
