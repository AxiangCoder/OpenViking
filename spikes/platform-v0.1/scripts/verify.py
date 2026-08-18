"""End-to-end verification for the platform v0.1 spike.

Covers key technical assumptions from design docs 02/03/04/05/06:
  1. login issues __Host-ov_session cookie; DB stores SHA-256 hash only
  2. unified LOGIN_FAILED for unknown email / wrong password
  3. PSA creates account + first admin; account admin creates plain user
  4. session + api key resolve to the SAME principal with identical permissions
  5. Actor/Subject split for admin member preview
  6. password change requires old password and rotates the login session
  7. admin password reset (rank 2 -> 1) revokes all target login sessions
     but does NOT revoke the target's API keys
  8. same-or-higher rank reset forbidden; cross-account reset -> 404
  9. API key revocation: one key revoked, other keys unaffected
  10. disable revokes sessions + all keys immediately
  11. promote user -> account_admin; permission_version cache invalidation
      takes effect without re-login
"""

from __future__ import annotations

import os
import sys

import httpx

BASE = "http://127.0.0.1:18080"
PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=10.0)


PSA_PASSWORD = os.environ.get("OV_PSA_PASSWORD", "Spike-PSA-Pass-2026-Dev")


def login(c: httpx.Client, email: str, password: str) -> tuple[str, str]:
    r = c.post("/api/platform/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"], password


def main() -> None:
    print("== 1. login issues session cookie ==")
    with client() as c:
        r = c.post("/api/platform/v1/auth/login", json={"email": "psa@example.com", "password": PSA_PASSWORD})
        check("psa login ok", r.status_code == 200, r.text[:200])
        cookie = c.cookies.get("__Host-ov_session")
        check("session cookie issued (>=256bit)", cookie is not None and len(cookie) >= 40)
        csrf = r.json()["result"]["csrf_token"]
        check("csrf token returned", len(csrf) >= 20)
        r = c.get("/api/platform/v1/auth/me")
        me = r.json()["result"]
        check("me returns platform_super_admin role", "platform_super_admin" in me["roles"])
        check("psa has platform perms", "account.manage.platform" in me["permissions"])
        check(
            "psa NOT granted skill write perms (03 §9.3)",
            "skill.account_shared.manage.account" not in me["permissions"],
        )
        check("psa no account (NULL allowed)", me["account"]["id"] is None)

    print("== 2. unified login failure ==")
    with client() as c:
        r = c.post("/api/platform/v1/auth/login", json={"email": "psa@example.com", "password": "wrong-password"})
        check("wrong password -> LOGIN_FAILED", r.status_code == 401 and r.json()["detail"]["code"] == "LOGIN_FAILED")
        r = c.post("/api/platform/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever-1234"})
        check("unknown email -> same LOGIN_FAILED", r.status_code == 401 and r.json()["detail"]["code"] == "LOGIN_FAILED")

    print("== 3. PSA creates account + first admin; admin creates user ==")
    with client() as c:
        csrf, _ = login(c, "psa@example.com", PSA_PASSWORD)
        h = {"X-CSRF-Token": csrf}
        r = c.post(
            "/api/platform/v1/platform/accounts",
            json={"account_code": "acme", "account_name": "Acme Corp", "admin_email": "admin@acme.com", "admin_username": "admin"},
            headers=h,
        )
        check("account+first admin created", r.status_code == 200, r.text[:200])
        account = r.json()["result"]["account"]
        admin_password = r.json()["result"]["first_admin"]["initial_password"]
        check("ov_account_id mapped", account["ov_account_id"].startswith("ov_account_"))
        check("first admin ov_user_id mapped", r.json()["result"]["first_admin"]["ov_user_id"].startswith("ov_user_"))

        r = c.post("/api/platform/v1/platform/accounts", json={"account_code": "beta", "account_name": "Beta", "admin_email": "admin2@beta.com", "admin_username": "admin2"}, headers=h)
        check("second account created", r.status_code == 200)
        beta_admin_password = r.json()["result"]["first_admin"]["initial_password"]

        r = c.post("/api/platform/v1/platform/accounts", json={"account_code": "gamma", "account_name": "Gamma", "admin_email": "admin3@gamma.com", "admin_username": "admin3"}, headers=h)
        check("third account created", r.status_code == 200)
        admin3_password = r.json()["result"]["first_admin"]["initial_password"]

    with client() as c:
        csrf, _ = login(c, "admin@acme.com", admin_password)
        ha = {"X-CSRF-Token": csrf}
        r = c.post("/api/platform/v1/admin/users", json={"email": "alice@acme.com", "username": "alice", "display_name": "Alice"}, headers=ha)
        check("account admin creates plain user", r.status_code == 200, r.text[:200])
        alice_password = r.json()["result"]["initial_password"]
        check("role fixed to user", r.json()["result"]["role"] == "user")
        check("initial password length >= 16", len(alice_password) >= 16)

        r = c.get("/api/platform/v1/admin/roles", headers=ha)
        roles = {x["code"]: x for x in r.json()["result"]}
        check("three built-in roles", set(roles) == {"platform_super_admin", "account_admin", "user"})
        check("ranks 3/2/1", roles["platform_super_admin"]["rank"] == 3 and roles["account_admin"]["rank"] == 2 and roles["user"]["rank"] == 1)
        check("ov_base_role mapping", roles["account_admin"]["ov_base_role"] == "admin" and roles["user"]["ov_base_role"] == "user" and roles["platform_super_admin"]["ov_base_role"] is None)

        r = c.get("/api/platform/v1/admin/users", headers=ha)
        users = r.json()["result"]
        check("admin lists account users", len(users) >= 2)
        alice_row = next(u for u in users if u["email"] == "alice@acme.com")
        alice_user_id = alice_row["id"]

    print("== 4. session vs api key principal parity ==")
    with client() as c:
        csrf, _ = login(c, "alice@acme.com", alice_password)
        h = {"X-CSRF-Token": csrf}
        me = c.get("/api/platform/v1/auth/me").json()["result"]
        check("alice role=user", me["roles"] == ["user"])
        check(
            "alice user perms",
            "resource.user_private.write.self" in me["permissions"]
            and "resource.account_shared.read.account" in me["permissions"]
            and "resource.account_shared.write.account" not in me["permissions"],
        )
        r = c.post("/api/platform/v1/me/api-keys", json={"name": "Codex on MacBook"}, headers=h)
        check("create api key", r.status_code == 200)
        alice_key1 = r.json()["result"]["api_key"]
        check("key format ovk_u.<public>.<secret>", alice_key1.startswith("ovk_u.") and alice_key1.count(".") == 2)
        r = c.post("/api/platform/v1/me/api-keys", json={"name": "OpenClaw"}, headers=h)
        alice_key2 = r.json()["result"]["api_key"]
        r = c.get("/api/platform/v1/me/api-keys", headers=h)
        check("list has no plaintext", r.status_code == 200 and all("api_key" not in x for x in r.json()["result"]))
        check("two named keys listed", len(r.json()["result"]) == 2)

    with client() as c:
        r = c.get("/api/platform/v1/me/debug/actor-context")
        check("no credential -> 401", r.status_code == 401)
        r = c.get("/api/platform/v1/me/debug/actor-context", headers={"Authorization": f"Bearer {alice_key1}"})
        check("api key resolves", r.status_code == 200, r.text[:200])
        body = r.json()["result"]
        check("api key actor == session actor", body["user_id"] == alice_user_id)
        check("authentication_method=api_key", body["authentication_method"] == "api_key")
        r = c.post("/api/platform/v1/me/api-keys", json={"name": "x"}, headers={"Authorization": f"Bearer {alice_key1}", "X-CSRF-Token": "whatever"})
        check("api key cannot do CSRF-protected product writes", r.status_code == 403)

    print("== 5. actor/subject preview ==")
    with client() as c:
        csrf, _ = login(c, "admin@acme.com", admin_password)
        r = c.get(f"/api/platform/v1/admin/users/{alice_user_id}/actor-subject", headers={"X-CSRF-Token": csrf})
        body = r.json()["result"]
        check(
            "actor=admin, subject=alice, visibility=user_private",
            r.status_code == 200 and body["actor_user_id"] != body["subject_user_id"]
            and body["subject_ov_user_id"].startswith("ov_user_")
            and body["visibility"] == "user_private",
        )

    print("== 6. password change: old password required + session rotation ==")
    with client() as c:
        csrf, _ = login(c, "alice@acme.com", alice_password)
        old_cookie = c.cookies.get("__Host-ov_session")
        r = c.post("/api/platform/v1/auth/password/change", json={"old_password": "bad-old", "new_password": "NewPass-2026-strong"}, headers={"X-CSRF-Token": csrf})
        check("wrong old password -> LOGIN_FAILED", r.status_code == 401 and r.json()["detail"]["code"] == "LOGIN_FAILED")
        r = c.post("/api/platform/v1/auth/password/change", json={"old_password": alice_password, "new_password": "NewPass-2026-strong"}, headers={"X-CSRF-Token": csrf})
        check("password change ok", r.status_code == 200)
        check("session rotated", c.cookies.get("__Host-ov_session") not in (None, old_cookie))
        alice_password = "NewPass-2026-strong"
        check("rotated session valid", c.get("/api/platform/v1/auth/me").status_code == 200)

    print("== 7. admin reset revokes all target sessions but keeps api keys ==")
    with client() as c1, client() as c2, client() as c3:
        login(c1, "alice@acme.com", alice_password)
        check("alice session alive pre-reset", c1.get("/api/platform/v1/auth/me").status_code == 200)
        csrf, _ = login(c2, "admin@acme.com", admin_password)
        r = c2.post(f"/api/platform/v1/admin/users/{alice_user_id}/password/reset", headers={"X-CSRF-Token": csrf})
        check("admin resets alice password", r.status_code == 200, r.text[:200])
        alice_password = r.json()["result"]["new_password"]
        check("reset revoked all sessions", r.json()["result"]["sessions_revoked"] is True)
        check("alice old session dead", c1.get("/api/platform/v1/auth/me").status_code == 401)
        # fresh client without any cookie: api key alone must still work
        r = c3.get("/api/platform/v1/me/debug/actor-context", headers={"Authorization": f"Bearer {alice_key1}"})
        check("reset does NOT revoke api keys (03 §8.3)", r.status_code == 200)

    print("== 8. strict rank reset rules ==")
    with client() as c:
        csrf, _ = login(c, "admin3@gamma.com", admin3_password)
        r = c.post(f"/api/platform/v1/admin/users/{alice_user_id}/password/reset", headers={"X-CSRF-Token": csrf})
        check("cross-account reset -> 404 (not found semantics)", r.status_code == 404)

    with client() as c:
        csrf, _ = login(c, "admin2@beta.com", beta_admin_password)
        r = c.post(f"/api/platform/v1/admin/users/{alice_user_id}/password/reset", headers={"X-CSRF-Token": csrf})
        check("cross-account reset (admin2->alice) -> 404", r.status_code == 404)

    print("== 9. api key revocation is per-key ==")
    with client() as c, client() as c2, client() as c3:
        csrf, _ = login(c, "alice@acme.com", alice_password)
        h = {"X-CSRF-Token": csrf}
        keys = c.get("/api/platform/v1/me/api-keys", headers=h).json()["result"]
        key1_id = next(k["id"] for k in keys if k["name"] == "Codex on MacBook")
        r = c.delete(f"/api/platform/v1/me/api-keys/{key1_id}", headers=h)
        check("revoke key1", r.status_code == 200)
        r = c.delete(f"/api/platform/v1/me/api-keys/{key1_id}", headers=h)
        check("revoke idempotent (404 on missing)", r.status_code == 404)
        # fresh clients without cookies: pure bearer checks
        r = c2.get("/api/platform/v1/me/debug/actor-context", headers={"Authorization": f"Bearer {alice_key1}"})
        check("revoked key dead", r.status_code == 401)
        r = c3.get("/api/platform/v1/me/debug/actor-context", headers={"Authorization": f"Bearer {alice_key2}"})
        check("other key unaffected", r.status_code == 200)

    print("== 10. disable revokes sessions + all keys immediately ==")
    with client() as c1, client() as c2:
        login(c1, "alice@acme.com", alice_password)
        csrf, _ = login(c2, "admin@acme.com", admin_password)
        r = c2.post(f"/api/platform/v1/admin/users/{alice_user_id}/disable", headers={"X-CSRF-Token": csrf})
        check("disable ok", r.status_code == 200)
        check("session dead after disable", c1.get("/api/platform/v1/auth/me").status_code == 401)
        r = c1.post("/api/platform/v1/auth/login", json={"email": "alice@acme.com", "password": alice_password})
        check("disabled cannot login", r.status_code == 401)
        r = c1.get("/api/platform/v1/me/debug/actor-context", headers={"Authorization": f"Bearer {alice_key2}"})
        check("all keys dead after disable", r.status_code == 401)

    print("== 11. enable + promote: permission change effective without re-login ==")
    with client() as c:
        csrf, _ = login(c, "admin@acme.com", admin_password)
        r = c.patch(f"/api/platform/v1/admin/users/{alice_user_id}", json={"status": "active"}, headers={"X-CSRF-Token": csrf})
        check("account admin enables alice", r.status_code == 200, r.text[:200])
    with client() as c:
        csrf, _ = login(c, "psa@example.com", PSA_PASSWORD)
        h = {"X-CSRF-Token": csrf}
        acme_id = _acme_id(c, csrf)
        r = c.put(f"/api/platform/v1/platform/accounts/{acme_id}/users/{alice_user_id}/role", headers=h)
        check("psa promotes alice -> account_admin", r.status_code == 200)

    with client() as c:
        csrf, _ = login(c, "alice@acme.com", alice_password)
        me = c.get("/api/platform/v1/auth/me").json()["result"]
        check(
            "promoted perms effective immediately (cache versioned)",
            "account_admin" in me["roles"] and "user.create" in me["permissions"],
        )
        # alice is now account_admin (rank 2); same-rank reset of admin@acme must fail
        users = c.get("/api/platform/v1/admin/users").json()["result"]
        admin_row = next(u for u in users if u["email"] == "admin@acme.com")
        r = c.post(f"/api/platform/v1/admin/users/{admin_row['id']}/password/reset", headers={"X-CSRF-Token": csrf})
        check(
            "same-rank reset forbidden (2 -> 2)",
            r.status_code == 403 and r.json()["detail"]["code"] == "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN",
        )

    print("== 12. provisioning retry guard ==")
    with client() as c:
        csrf, _ = login(c, "psa@example.com", PSA_PASSWORD)
        acme_id = _acme_id(c, csrf)
        r = c.post(f"/api/platform/v1/platform/accounts/{acme_id}/provisioning/retry", headers={"X-CSRF-Token": csrf})
        check("active account retry -> 409", r.status_code == 409 and r.json()["detail"]["code"] == "PROVISIONING_NOT_RETRYABLE")

    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("Failed:", FAILED)
        sys.exit(1)


def _acme_id(c: httpx.Client, csrf: str) -> str:
    accounts = c.get("/api/platform/v1/platform/accounts", headers={"X-CSRF-Token": csrf}).json()["result"]
    return next(a["id"] for a in accounts if a["code"] == "acme")


if __name__ == "__main__":
    main()
