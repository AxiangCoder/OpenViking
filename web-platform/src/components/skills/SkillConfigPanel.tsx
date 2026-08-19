/**
 * Skill 私密配置面板（10 §53.3/§52.16，05 §12.5 三接口，P3-E5 AC⑤⑧）。
 *
 * - 仅当前 User 管理自己的配置（me/skill-configs，含自己的私有与当前 Account
 *   共享 Skill）；
 * - 状态区只展示脱敏值（value_masked=固定掩码），响应不含可恢复 Secret（AC⑧）；
 * - 「保存新版本」以 key=value 行输入完整新值；历史版本可单独激活；
 * - 保存/激活后刷新快照；版本内容变化才递增（重复提交返回当前版本）。
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { isPlatformError } from "@/lib/platform-client";
import {
  activateSkillConfig,
  getSkillConfig,
  putSkillConfig,
  skillErrorMessage,
  type SkillConfigSnapshot,
} from "@/features/skills";

export function SkillConfigPanel({ skillId }: { skillId: string }) {
  const [snapshot, setSnapshot] = useState<SkillConfigSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [lines, setLines] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    getSkillConfig(skillId)
      .then(setSnapshot)
      .catch((error) => {
        setLoadError(isPlatformError(error) ? error.message || "无法加载私密配置" : "无法加载私密配置");
      });
  }, [skillId]);

  useEffect(() => {
    load();
  }, [load]);

  function parseLines(): Record<string, string> | null {
    const values: Record<string, string> = {};
    for (const rawLine of lines.split("\n")) {
      const line = rawLine.trim();
      if (!line) continue;
      const eq = line.indexOf("=");
      if (eq <= 0 || eq === line.length - 1) return null;
      values[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
    }
    if (Object.keys(values).length === 0) return null;
    return values;
  }

  function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = parseLines();
    if (values == null) {
      setActionError("每行必须是 key=value，且 key/value 均非空");
      return;
    }
    setBusy(true);
    setActionError(null);
    setNotice(null);
    putSkillConfig(skillId, values)
      .then((next) => {
        setSnapshot(next);
        setLines("");
        setShowForm(false);
        setNotice(`已保存新版本 v${next.latest_version ?? 1}（仅展示脱敏状态）。`);
      })
      .catch((error) => {
        setActionError(skillErrorMessage(error));
      })
      .finally(() => setBusy(false));
  }

  function handleActivate(version: number) {
    if (busy) return;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    activateSkillConfig(skillId, version)
      .then((next) => {
        setSnapshot(next);
        setNotice(`已激活版本 v${version}。`);
      })
      .catch((error) => {
        setActionError(skillErrorMessage(error));
      })
      .finally(() => setBusy(false));
  }

  return (
    <div data-testid="skill-config-panel">
      {loadError ? (
        <p className="login-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {snapshot == null && !loadError ? <p>加载中…</p> : null}
      {snapshot ? (
        <div>
          <p className="profile-hint" data-testid="skill-config-status">
            {snapshot.configured
              ? `已配置 · 当前激活 v${snapshot.active_version ?? "—"} · 最新 v${snapshot.latest_version ?? "—"}`
              : "未配置"}
            {snapshot.configured ? " · 仅本人可见，发布/共享不迁移（10 §52.16）" : ""}
          </p>
          {snapshot.values.length > 0 ? (
            <table className="data-table" data-testid="skill-config-values">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>值（脱敏）</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.values.map((item) => (
                  <tr key={item.key} data-testid={`skill-config-value-${item.key}`}>
                    <td>{item.key}</td>
                    <td>
                      <code className="skill-config-mask" data-testid={`skill-config-mask-${item.key}`}>
                        {item.value_masked}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {snapshot.versions.length > 0 ? (
            <div className="skill-config-versions" data-testid="skill-config-versions">
              <span className="profile-hint">历史版本：</span>
              {snapshot.versions.map((version) => (
                <button
                  key={version}
                  type="button"
                  className="skill-version-chip"
                  disabled={version === snapshot.active_version || busy}
                  onClick={() => handleActivate(version)}
                  data-testid={`skill-config-activate-${version}`}
                >
                  v{version}
                  {version === snapshot.active_version ? "（激活中）" : ""}
                </button>
              ))}
            </div>
          ) : null}
          {actionError ? (
            <p className="login-error" role="alert" data-testid="skill-config-error">
              {actionError}
            </p>
          ) : null}
          {notice ? (
            <p className="profile-hint" role="status" data-testid="skill-config-notice">
              {notice}
            </p>
          ) : null}
          {showForm ? (
            <form className="profile-form skill-config-form" onSubmit={handleSave} data-testid="skill-config-form">
              <label className="login-field">
                <span>保存新版本（每行 key=value；新值仅本次输入，保存后只展示脱敏状态）</span>
                <textarea
                  name="values"
                  rows={5}
                  value={lines}
                  onChange={(e) => setLines(e.target.value)}
                  disabled={busy}
                  data-testid="skill-config-lines"
                />
              </label>
              <div className="profile-actions">
                <button type="submit" className="primary-button" disabled={busy} data-testid="skill-config-save">
                  {busy ? "保存中…" : "保存新版本"}
                </button>
                <button type="button" onClick={() => setShowForm(false)} disabled={busy} data-testid="skill-config-cancel">
                  取消
                </button>
              </div>
            </form>
          ) : (
            <div className="profile-actions">
              <button type="button" className="primary-button" onClick={() => setShowForm(true)} data-testid="skill-config-edit">
                {snapshot.configured ? "保存新版本" : "配置"}
              </button>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
