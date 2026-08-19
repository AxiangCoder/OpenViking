/**
 * /admin/roles 角色与权限页（13 §87.1，03 §9.2/9.3，14 号计划 §98.8，P4-E3 AC①）。
 *
 * - 只读展示三个内置角色（platform_super_admin / account_admin / user）与权限矩阵；
 * - v0.1 仅三内置角色：无角色创建、编辑、删除、分配入口（03 §9.2，AC①）；
 * - 权限矩阵按 `<domain>` 分区展示，行 = 权限 code，列 = 三角色，✓/—；
 * - 加载失败保留页面框架，展示 Request ID 与重试（13 §84.3）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { isPlatformError } from "@/lib/platform-client";
import { useMe } from "@/features/auth/useAuth";
import { ROLES } from "@/lib/permissions";
import {
  fetchAdminRoles,
  permissionDomain,
  type RoleView,
} from "@/features/iam/admin-governance";

const ROLE_LABELS: Record<string, string> = {
  [ROLES.PLATFORM_SUPER_ADMIN]: "Platform Super Admin",
  [ROLES.ACCOUNT_ADMIN]: "Account Admin",
  [ROLES.USER]: "User",
};

function roleLabel(code: string): string {
  return ROLE_LABELS[code] ?? code;
}

export default function AdminRolesPage() {
  const me = useMe();
  const [roles, setRoles] = useState<RoleView[] | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; requestId: string | null } | null>(
    null,
  );

  const load = useCallback(() => {
    setLoadError(null);
    setRoles(null);
    fetchAdminRoles()
      .then(setRoles)
      .catch((error) => {
        setLoadError({
          message: isPlatformError(error) ? error.message || "无法加载角色列表" : "无法加载角色列表",
          requestId: isPlatformError(error) ? error.requestId : null,
        });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const isAdmin = (me?.roles ?? []).includes(ROLES.ACCOUNT_ADMIN);

  // 矩阵：行 = 去重后权限 code（按 domain 分区），列 = 三内置角色
  const matrix = useMemo(() => {
    if (!roles) return { domains: [] as { domain: string; rows: string[] }[], roleByCode: new Map<string, RoleView>() };
    const byDomain = new Map<string, Set<string>>();
    for (const role of roles) {
      for (const code of role.permissions) {
        const domain = permissionDomain(code);
        if (!byDomain.has(domain)) byDomain.set(domain, new Set());
        byDomain.get(domain)!.add(code);
      }
    }
    return {
      domains: [...byDomain.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([domain, codes]) => ({ domain, rows: [...codes].sort() })),
      roleByCode: new Map(roles.map((role) => [role.code, role])),
    };
  }, [roles]);

  return (
    <div className="admin-page" data-testid="admin-roles-page">
      <div className="admin-page-header">
        <div>
          <h2>角色与权限</h2>
          <p className="admin-account-context">
            只读展示三个内置角色与权限矩阵（03 §9.2/9.3，AC①）
          </p>
        </div>
      </div>

      <p className="profile-hint" data-testid="roles-readonly-note">
        v0.1 仅提供三个内置角色，系统不允许创建、编辑、删除角色或分配角色权限，本页无任何管理入口。
        {isAdmin ? "（当前身份：Account Admin）" : null}
      </p>

      {loadError ? (
        <div className="admin-load-error" role="alert" data-testid="admin-roles-load-error">
          <p className="login-error">{loadError.message}</p>
          {loadError.requestId ? (
            <p className="error-page-request-id">
              请求 ID：<code>{loadError.requestId}</code>
            </p>
          ) : null}
          <div className="profile-actions">
            <button type="button" onClick={load} data-testid="admin-roles-retry">
              重试
            </button>
          </div>
        </div>
      ) : null}

      {roles == null && !loadError ? (
        <p className="profile-hint" data-testid="admin-roles-loading">
          加载中…
        </p>
      ) : null}

      {roles != null ? (
        <>
          <section className="card" data-testid="admin-roles-list">
            <table className="data-table">
              <thead>
                <tr>
                  <th>角色</th>
                  <th>数据范围</th>
                  <th>说明</th>
                  <th>权限数</th>
                </tr>
              </thead>
              <tbody>
                {roles.map((role) => (
                  <tr key={role.id} data-testid={`admin-role-row-${role.code}`}>
                    <td>
                      <span className="role-badge">{roleLabel(role.code)}</span>
                      <span className="resource-muted">（{role.code}）</span>
                    </td>
                    <td>{roleDescription(role.code)}</td>
                    <td>{role.description ?? "—"}</td>
                    <td>{role.permissions.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="card" data-testid="admin-role-matrix">
            <h3 className="page-title">权限矩阵</h3>
            {matrix.domains.map(({ domain, rows }) => (
              <div key={domain} data-testid={`matrix-domain-${domain}`}>
                <h4 className="recycle-group-title">{domain}</h4>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>权限</th>
                      {roles.map((role) => (
                        <th key={role.id}>{roleLabel(role.code)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((code) => (
                      <tr key={code} data-testid={`matrix-row-${code}`}>
                        <td>
                          <code>{code}</code>
                        </td>
                        {roles.map((role) => (
                          <td key={role.id}>
                            {matrix.roleByCode.get(role.code)?.permissions.includes(code) ? (
                              <span className="status-badge active">✓</span>
                            ) : (
                              <span className="resource-muted">—</span>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </section>
        </>
      ) : null}
    </div>
  );
}

function roleDescription(code: string): string {
  switch (code) {
    case ROLES.PLATFORM_SUPER_ADMIN:
      return "全平台";
    case ROLES.ACCOUNT_ADMIN:
      return "当前 Account";
    case ROLES.USER:
      return "自己 + 当前 Account 共享只读/使用";
    default:
      return "—";
  }
}
