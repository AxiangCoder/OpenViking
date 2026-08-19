#!/usr/bin/env bash
# P5-E2（14 号计划 §99.2，06 §14.4）+ P5-E3（§99.3 选型固化，06 §15.4）：
# PG 备份加密 + 恢复审计脚本（生产可执行）。
#
# 选型结论（p5-e3-backup-tooling.md）：pg_dump -Fc → gzip → GPG 对称 AES256；
# 恢复 = 解密 → pg_restore --clean；备份与恢复必须审计（06 §14.4/§15.4）。
#
# 子命令：
#   backup [outdir]                       PG custom 格式 dump → gzip → GPG 密文
#   backup-volume <srcdir> [outdir]       OpenViking 数据卷 tar → gzip → GPG 密文
#   restore <backup.enc.gz>               PG 覆盖恢复（需确认；演练可跳过确认）
#   restore-volume <vol.tar.gz.gpg> <destdir>  数据卷解包恢复（校验路径前缀）
#   verify <backup.enc.gz>                GPG 解密校验 + pg_restore --list + 抽样行数
#   list <backup.enc.gz>                  仅列出备份内容（pg_restore --list）
#
# 环境变量：
#   OV_PLATFORM_DATABASE_URL        目标 PG DSN（backup/restore/verify 需要）
#   OV_PLATFORM_BACKUP_PASSPHRASE   GPG 对称口令（Secret Manager 注入）
#   OV_PLATFORM_BACKUP_CONFIRM=yes  恢复跳过交互确认（演练/自动化用）
#   OV_PLATFORM_BACKUP_AUDIT_LOG    审计日志文件（默认 ./audit/ops-audit.log，权限 600）
#   OV_PLATFORM_BACKUP_AUDIT_DB_URL 可选：备份/恢复审计同时写入 iam_audit_events
#   OV_PLATFORM_PG_TOOLS            可选：PG 工具调用前缀（本地无 pg_dump/psql 时用
#                                   docker exec -i <容器> 执行，例如
#                                   OV_PLATFORM_PG_TOOLS="docker exec -i ovp-postgresql"）
set -euo pipefail

DB_URL="${OV_PLATFORM_DATABASE_URL:-}"
PASSPHRASE="${OV_PLATFORM_BACKUP_PASSPHRASE:?set OV_PLATFORM_BACKUP_PASSPHRASE (GPG symmetric key)}"
CONFIRM="${OV_PLATFORM_BACKUP_CONFIRM:-no}"
TMPDIR_OV="${OV_PLATFORM_BACKUP_TMPDIR:-/tmp}"
PG_TOOLS="${OV_PLATFORM_PG_TOOLS:-}"
PG_DSN="${DB_URL#postgresql://}"

# DSN 解析（纯 bash，无 sed，跨 GNU/BSD/macOS）：user:pass@host:port/db
_rest="${PG_DSN#*@}"                                  # host:port/db
_host_port="${_rest%%/*}"                             # host:port
PG_HOST="${_host_port%%:*}"
PG_PORT="${_host_port#*:}"
[ "$PG_PORT" = "$_host_port" ] && PG_PORT="5432"      # 无端口 → 5432
PG_USER="${PG_DSN%%:*}"
PG_PASS="${PG_DSN#*:}"; PG_PASS="${PG_PASS%@*}"
PG_DB="${PG_DSN##*/}"

pg_host() { echo "$PG_HOST"; }
pg_port() { echo "$PG_PORT"; }
pg_user() { echo "$PG_USER"; }
pg_db() { echo "$PG_DB"; }
pg_pass() { echo "$PG_PASS"; }

# 执行 PG 工具：本地二进制，或经 OV_PLATFORM_PG_TOOLS 前缀（docker exec）转发。
# pg_dump -Fc 以 stdout 流式输出（docker exec 可回传宿主机），pg_restore/psql 读 stdin；
# docker exec 需经 `env` 显式转发 PGPASSWORD（docker 不自动继承客户端环境）。
pg() { if [ -n "$PG_TOOLS" ]; then $PG_TOOLS env "PGPASSWORD=${PGPASSWORD:-}" "$@"; else "$@"; fi; }

# pg_restore 需要 seekable 文件（custom 格式不能读管道）：docker 模式先 docker cp 进容器。
# PG_TOOLS 末词约定为容器名（如 "docker exec -i ovp-postgresql"）。
pg_restore_cmd() { # pg_restore_cmd <hostfile> [pg_restore args...]
  local hostfile="$1"; shift
  if [ -n "$PG_TOOLS" ]; then
    local cname="${PG_TOOLS##* }" cpath="/tmp/_ov_restore_$$.dump"
    docker cp "$hostfile" "$cname:$cpath" >/dev/null
    $PG_TOOLS env "PGPASSWORD=${PGPASSWORD:-}" pg_restore "$@" "$cpath"
  else
    pg_restore "$@" "$hostfile"
  fi
}

cmd="${1:-}"
who="${OV_PLATFORM_BACKUP_OPERATOR:-$(id -un)}"

audit_log="${OV_PLATFORM_BACKUP_AUDIT_LOG:-./audit/ops-audit.log}"
mkdir -p "$(dirname "$audit_log")"
[ -e "$audit_log" ] && chmod 600 "$audit_log" || touch "$audit_log"

audit() { # audit <kind> <result> <detail>（append-only，权限 600）
  printf '[%s] %s kind=%s result=%s operator=%s detail="%s"\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$0")" "$1" "$2" "$who" "$3" >>"$audit_log"
}

# 审计写入 iam_audit_events（可选；action=backup.created / backup.restore，
# actor_type=system、actor_system_component=backup-ops，06 §14.4 同表审计）。
audit_db() { # audit_db <action> <result> <target> <detail>
  [ -n "${OV_PLATFORM_BACKUP_AUDIT_DB_URL:-}" ] || return 0
  local action="$1" result="$2" target="$3" detail="$4"
  local adsn host port user pass db
  adsn="${OV_PLATFORM_BACKUP_AUDIT_DB_URL#postgresql://}"
  host="${adsn#*@}"; host="${host%%:*}"
  port="$(echo "${adsn#*@}" | sed -nE 's/.*:([0-9]+)\/.*/\1/p')"; port="${port:-5432}"
  user="${adsn%%:*}"; pass="$(echo "${adsn#*:}" | sed -E 's/^([^@]*)@.*/\1/')"; db="${adsn##*/}"
  PGPASSWORD="$pass" pg psql -h "$host" -p "$port" -U "$user" -d "$db" \
    -v ON_ERROR_STOP=1 -qAt -c "INSERT INTO iam_audit_events (
      actor_type, actor_system_component, action, target_type, target_id,
      scope, result, metadata) VALUES (
      'system', 'backup-ops', '$action', 'postgres', '$target',
      'platform', '$result',
      jsonb_build_object('operator', '$who', 'backup_file', '$detail'))" >/dev/null 2>&1 || return 0
}

case "$cmd" in
  backup)
    [ -n "$DB_URL" ] || { echo "[backup] OV_PLATFORM_DATABASE_URL required" >&2; exit 1; }
    outdir="${2:-./backups}"
    mkdir -p "$outdir"
    ts="$(date -u +%Y%m%d-%H%M%SZ)"
    enc="$outdir/ov_platform_${ts}.dump.gz.gpg"
    echo "[backup] dumping $(pg_db)@$(pg_host):$(pg_port) ..."
    PGPASSWORD="$(pg_pass)" pg pg_dump -h "$(pg_host)" -p "$(pg_port)" -U "$(pg_user)" \
      -Fc --no-owner --no-privileges "$(pg_db)" | gzip | gpg --batch --yes \
      --pinentry-mode loopback --passphrase "$PASSPHRASE" --symmetric \
      --cipher-algo AES256 --output "$enc"
    chmod 600 "$enc"
    size="$(stat -f%z "$enc" 2>/dev/null || stat -c%s "$enc")"
    echo "[backup] encrypted backup written: $enc ($size bytes)"
    audit backup created "$(basename "$enc") ($size bytes)"
    audit_db backup.created success "$(pg_db)" "$(basename "$enc")"
    ;;
  backup-volume)
    src="${2:?usage: backup-volume <srcdir> [outdir]}"
    outdir="${3:-./backups}"
    [ -d "$src" ] || { echo "[backup-volume] srcdir not found: $src" >&2; exit 1; }
    mkdir -p "$outdir"
    ts="$(date -u +%Y%m%d-%H%M%SZ)"
    enc="$outdir/ov_volume_${ts}.tar.gz.gpg"
    tar -czf - -C "$(dirname "$src")" "$(basename "$src")" | gpg --batch --yes \
      --pinentry-mode loopback --passphrase "$PASSPHRASE" --symmetric \
      --cipher-algo AES256 --output "$enc"
    chmod 600 "$enc"
    size="$(stat -f%z "$enc" 2>/dev/null || stat -c%s "$enc")"
    echo "[backup-volume] encrypted volume backup written: $enc ($size bytes)"
    audit backup-volume created "$(basename "$enc") src=$(basename "$src") ($size bytes)"
    ;;
  restore)
    [ -n "$DB_URL" ] || { echo "[restore] OV_PLATFORM_DATABASE_URL required" >&2; exit 1; }
    enc="${2:?usage: restore <backup.enc.gz>}"
    [ -f "$enc" ] || { echo "[restore] backup file not found: $enc" >&2; exit 1; }
    if [ "$CONFIRM" != "yes" ]; then
      echo "[restore] WARNING: 恢复将覆盖目标数据库 $(pg_db)@$(pg_host):$(pg_port)；恢复操作必须审计。" >&2
      read -r -p "确认恢复 $enc ? [yes/N] " confirm
      [ "$confirm" = "yes" ] || { echo aborted; exit 1; }
    fi
    tmp="$TMPDIR_OV/_restore_$$.dump"
    trap 'rm -f "$tmp"' EXIT
    audit restore started "$(basename "$enc")"
    audit_db backup.restore started "$(pg_db)" "$(basename "$enc")"
    gpg --batch --yes --pinentry-mode loopback --passphrase "$PASSPHRASE" \
      --decrypt "$enc" | gunzip > "$tmp"
    PGPASSWORD="$(pg_pass)" pg_restore_cmd "$tmp" -h "$(pg_host)" -p "$(pg_port)" -U "$(pg_user)" \
      -d "$(pg_db)" --clean --if-exists --no-owner
    echo "[restore] done."
    audit restore completed "$(basename "$enc")"
    audit_db backup.restore completed "$(pg_db)" "$(basename "$enc")"
    ;;
  restore-volume)
    enc="${2:?usage: restore-volume <vol.tar.gz.gpg> <destdir>}"
    dest="${3:?usage: restore-volume <vol.tar.gz.gpg> <destdir>}"
    [ -f "$enc" ] || { echo "[restore-volume] backup file not found: $enc" >&2; exit 1; }
    if [ "$CONFIRM" != "yes" ]; then
      read -r -p "确认恢复数据卷 $enc ? [yes/N] " confirm
      [ "$confirm" = "yes" ] || { echo aborted; exit 1; }
    fi
    tmp="$TMPDIR_OV/_vol_$$.tar.gz"
    trap 'rm -f "$tmp"' EXIT
    audit restore-volume started "$(basename "$enc")"
    gpg --batch --yes --pinentry-mode loopback --passphrase "$PASSPHRASE" \
      --decrypt "$enc" > "$tmp"
    bad="$(tar -tzf "$tmp" | grep -E '^/|\.\.' || true)"
    [ -z "$bad" ] || { echo "[restore-volume] unsafe archive entries detected" >&2; exit 1; }
    mkdir -p "$dest"
    tar -xzf "$tmp" -C "$dest"
    echo "[restore-volume] done."
    audit restore-volume completed "$(basename "$enc")"
    ;;
  verify)
    enc="${2:?usage: verify <backup.enc.gz>}"
    [ -f "$enc" ] || { echo "[verify] backup file not found: $enc" >&2; exit 1; }
    tmp="$TMPDIR_OV/_verify_$$.dump"
    trap 'rm -f "$tmp"' EXIT
    echo "[verify] decrypting ..."
    gpg --batch --yes --pinentry-mode loopback --passphrase "$PASSPHRASE" \
      --decrypt "$enc" | gunzip > "$tmp"
    echo "[verify] pg_restore --list 解析 ..."
    pg_restore_cmd "$tmp" --list >/dev/null
    echo "[verify] dump 可读、条目可列出：OK"
    if [ -n "${OV_PLATFORM_BACKUP_VERIFY_DB:-}" ]; then
      echo "[verify] 抽样行数对照 ${OV_PLATFORM_BACKUP_VERIFY_DB} ..."
      for t in iam_accounts iam_users iam_audit_events iam_outbox; do
        got="$(PGPASSWORD="$(pg_pass)" pg psql -h "$(pg_host)" -p "$(pg_port)" -U "$(pg_user)" \
          -d "${OV_PLATFORM_BACKUP_VERIFY_DB}" -Atc "select count(*) from $t" 2>/dev/null || echo 'NA')"
        echo "  $t = $got"
      done
    fi
    ;;
  list)
    enc="${2:?usage: list <backup.enc.gz>}"
    [ -f "$enc" ] || { echo "[list] backup file not found: $enc" >&2; exit 1; }
    tmp="$TMPDIR_OV/_list_$$.dump"
    trap 'rm -f "$tmp"' EXIT
    gpg --batch --yes --pinentry-mode loopback --passphrase "$PASSPHRASE" \
      --decrypt "$enc" | gunzip > "$tmp"
    pg_restore_cmd "$tmp" --list
    ;;
  *)
    echo "usage: $0 backup|backup-volume|restore|restore-volume|verify|list [args]" >&2
    exit 1
    ;;
esac
