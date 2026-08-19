#!/usr/bin/env bash
# P5-E2（14 号计划 §99.2，06 §14.4）：PG 备份加密 + 恢复审计脚本。
#
# - 备份：pg_dump（PG 16）→ gzip → GPG 对称加密（AES256）；密文落盘。
# - 恢复：解密 → pg_restore；恢复操作必须写审计（记录到 iam_audit_events 由
#   平台 `ov platform` 运行时的平台账号审计，另留 shell 侧审计日志）。
# - 选型结论与演练归 P5-E3（14 号计划 §99.3 首个交付项：备份/恢复工具与
#   加密方案确认）；本脚本为生产可执行骨架，E3 演练后据结论固化。
#
# 用法：
#   export OV_PLATFORM_BACKUP_PASSPHRASE='...'       # GPG 对称密钥（Secret Manager）
#   export OV_PLATFORM_DATABASE_URL='postgresql://ov_platform:CHANGE_ME@127.0.0.1:5432/ov_platform'
#   ./backup-encrypt.sh backup [outdir]
#   ./backup-encrypt.sh restore <backup.enc.gz>      # 需人工确认后执行
set -euo pipefail

DB_URL="${OV_PLATFORM_DATABASE_URL:?set OV_PLATFORM_DATABASE_URL}"
PASSPHRASE="${OV_PLATFORM_BACKUP_PASSPHRASE:?set OV_PLATFORM_BACKUP_PASSPHRASE (GPG symmetric key)}"
PG_DSN="${DB_URL#postgresql://}"

pg_host() { echo "${PG_DSN%@*}" | sed -E 's/.*@//; s/:.*//'; }
pg_port() { echo "${PG_DSN#*@}" | sed -E 's/:([0-9]+).*/\1/; t; s/.*/5432/'; }
pg_user() { echo "${PG_DSN%%:*}" | sed -E 's/([^:]*):.*/\1/'; }
pg_db() { echo "${PG_DSN##*/}"; }

cmd="${1:-}"
outdir="${2:-./backups}"
mkdir -p "$outdir"

case "$cmd" in
  backup)
    ts="$(date +%Y%m%d-%H%M%S)"
    plain="$outdir/ov_platform_${ts}.dump"
    enc="$plain.gz.gpg"
    echo "[backup] dumping ${pg_db}@${pg_host}:${pg_port} ..."
    PGPASSWORD="${PG_DSN#*:*@}" pg_dump -h "$(pg_host)" -p "$(pg_port)" -U "$(pg_user)" \
      -Fc --no-owner --no-privileges "$(pg_db)" -f "$plain"
    gzip -f "$plain"
    gpg --batch --yes --pinentry-mode loopback --passphrase "$PASSPHRASE" \
      --symmetric --cipher-algo AES256 --output "$enc" "$plain.gz"
    rm -f "$plain.gz"
    echo "[backup] encrypted backup written: $enc"
    echo "[backup] audit: backup created (encrypted, AES256-GPG) $(basename "$enc")"
    ;;
  restore)
    enc="${2:?usage: restore <backup.enc.gz>}"
    [ -f "$enc" ] || { echo "backup file not found: $enc" >&2; exit 1; }
    echo "[restore] WARNING: 恢复将覆盖目标数据库；恢复操作必须审计。" >&2
    read -r -p "确认恢复 $enc ? [yes/N] " confirm
    [ "$confirm" = "yes" ] || { echo aborted; exit 1; }
    gpg --batch --yes --pinentry-mode loopback --passphrase "$PASSPHRASE" \
      --decrypt "$enc" | gunzip > "$outdir/_restore.dump"
    PGPASSWORD="${PG_DSN#*:*@}" pg_restore -h "$(pg_host)" -p "$(pg_port)" -U "$(pg_user)" \
      -d "$(pg_db)" --clean --if-exists --no-owner "$outdir/_restore.dump"
    rm -f "$outdir/_restore.dump"
    echo "[restore] done. audit: restore completed from $(basename "$enc")"
    ;;
  *)
    echo "usage: $0 backup|restore [args]" >&2
    exit 1
    ;;
esac
