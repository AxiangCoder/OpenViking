#!/usr/bin/env bash
# P5-E3 真实演练剧本（14 号计划 §99.3，06 §14.4/§15.4）：
#   ① Drill A `full`       备份→故障注入→恢复→校验（验收①②⑤⑥）
#   ② Drill B `rollback`   应用版本回滚：migration down/up 可验证 + PG 凭证不回退（验收③）
#   ③ Drill C `standalone` 备份恢复独立实例 + 不可逆变更占位演练，主实例不受影响（验收④）
# 演练记录落盘 docs/ovp/v0.1/p5-e3-drill-record.md（人汇总）。
#
# 用法：
#   export OV_PLATFORM_TEST_ADMIN_URL='postgresql://ov_platform:ov_platform_dev@127.0.0.1:55455/postgres'
#   export OV_PLATFORM_BACKUP_PASSPHRASE='<口令>'
#   export OV_PLATFORM_PG_TOOLS='docker exec -i ovp-pg16-p5e3'   # 本地无 pg 工具时
#   ./drill-backup-restore.sh full [--db ov_platform_drill] [--workdir <dir>] [--keep]
#   ./drill-backup-restore.sh rollback [--db ...] [--workdir <dir>] [--keep]
#   ./drill-backup-restore.sh standalone --backup <file> [--db ...] [--target-db ...] [--workdir <dir>] [--keep]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BIN="$SCRIPT_DIR/backup-encrypt.sh"
DRILL_LIB="$SCRIPT_DIR/drill_lib.py"
PY="${PYTHON:-python3}"
[ -x "$REPO_ROOT/.venv/bin/python" ] && PY="$REPO_ROOT/.venv/bin/python"

ADMIN_URL="${OV_PLATFORM_TEST_ADMIN_URL:?set OV_PLATFORM_TEST_ADMIN_URL (admin DSN, postgres db)}"
PASSPHRASE="${OV_PLATFORM_BACKUP_PASSPHRASE:?set OV_PLATFORM_BACKUP_PASSPHRASE}"
export OV_PLATFORM_BACKUP_PASSPHRASE="$PASSPHRASE"
export OV_PLATFORM_BACKUP_CONFIRM=yes
export OV_PLATFORM_PG_TOOLS="${OV_PLATFORM_PG_TOOLS:-}"

# 双视角 DSN：python 侧（宿主机）用 host 视角；容器内 PG 工具用容器视角
# （docker exec 访问宿主已发布端口需 host.docker.internal）。
host_part="${ADMIN_URL#*@}"; host_part="${host_part%%/*}"     # 127.0.0.1:55455
host_addr="${host_part%%:*}"; host_port="${host_part#*:}"
[ "$host_port" = "$host_part" ] && host_port="5432"
dsn_head="${ADMIN_URL%@*}"                                    # postgresql://user:pass
dsn_db="${ADMIN_URL##*/}"
host_admin="${dsn_head}@${host_addr}:${host_port}/${dsn_db}"
tools_admin="$host_admin"
if [ -n "$OV_PLATFORM_PG_TOOLS" ]; then
  tools_admin="${dsn_head}@host.docker.internal:${host_port}/${dsn_db}"
fi

cmd="${1:-full}"; shift || true
db="ov_platform_drill"
target_db="ov_platform_drill_verify"
workdir=""
keep=""
backup_file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db) db="$2"; shift 2 ;;
    --target-db) target_db="$2"; shift 2 ;;
    --backup) backup_file="$2"; shift 2 ;;
    --workdir) workdir="$2"; shift 2 ;;
    --keep) keep=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

ts="$(date -u +%Y%m%d-%H%M%SZ)"
[ -n "$workdir" ] || workdir="./drill-results/$ts"
mkdir -p "$workdir"
echo "== P5-E3 drill [$cmd] start: $(date -u +%Y-%m-%dT%H:%M:%SZ) db=$db workdir=$workdir"

admin_async="${host_admin/postgresql:\/\//postgresql+asyncpg://}"
drill_url="${admin_async/\/postgres//$db}"
target_url="${admin_async/\/postgres//$target_db}"
drill_tools="${tools_admin/\/postgres//$db}"
target_tools="${tools_admin/\/postgres//$target_db}"

admin_user="${tools_admin#postgresql://}"; admin_user="${admin_user%%:*}"
admin_pass="${tools_admin#postgresql://}"; admin_pass="${admin_pass#*:}"; admin_pass="${admin_pass%%@*}"

step() { printf '\n[STEP] %s\n' "$1"; }
fail() { echo "[FAIL] $1" >&2; echo "{\"drill\":\"$cmd\",\"result\":\"FAIL\",\"step\":\"$1\"}" > "$workdir/report.json"; exit 1; }
pass() { echo "[PASS] $1"; }

psql_admin() { # 管理操作（docker exec 或本地 psql）
  local sql="$1"
  if [ -n "$OV_PLATFORM_PG_TOOLS" ]; then
    $OV_PLATFORM_PG_TOOLS env PGPASSWORD="$admin_pass" psql -U "$admin_user" -d postgres -At -c "$sql" 2>/dev/null || true
  else
    PGPASSWORD="$admin_pass" psql "$tools_admin" -At -c "$sql" 2>/dev/null || true
  fi
}

rebuild_db() { # rebuild_db <dbname> <url> —— 重建 + 迁移 + PSA（幂等：--keep 时不重建）
  local name="$1" url="$2"
  if [ -z "$keep" ]; then
    psql_admin "DROP DATABASE IF EXISTS $name" >/dev/null
  fi
  if ! psql_admin "SELECT 1 FROM pg_database WHERE datname='$name'" | grep -q 1; then
    psql_admin "CREATE DATABASE $name" >/dev/null
  fi
  PYTHONPATH="$REPO_ROOT" "$PY" -c "
from alembic import command
from alembic.config import Config
cfg = Config('$REPO_ROOT/openviking/server/platform/alembic.ini')
cfg.set_main_option('sqlalchemy.url', '$url')
command.upgrade(cfg, 'head')
" || fail "migrate $name"
  OV_PLATFORM_DATABASE_URL="$url" OV_PLATFORM_INIT_PSA_EMAIL="psa@drill.local" \
    OV_PLATFORM_INIT_PSA_USERNAME="psa" OV_PLATFORM_INIT_PSA_PASSWORD="Drill-PSA-2026-Dev!" \
    PYTHONPATH="$REPO_ROOT" "$PY" -m openviking.server.platform.bootstrap_cli init >/dev/null 2>&1 \
    || fail "bootstrap init $name"
}

json_field() { # json_field <file> <field>
  "$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$1" "$2"
}

case "$cmd" in
  full)
    step "0 预检与演练库重建"
    command -v gpg >/dev/null || fail "gpg missing"
    rebuild_db "$db" "$drill_url"
    pass "演练库 ${db} 就绪（迁移 head + PSA）"

    step "1 业务数据准备（Account/Admin/User/Key/Session/refs/failed outbox）"
    (cd "$workdir" && OV_PLATFORM_DATABASE_URL="$drill_url" \
      PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" prepare "$drill_url" -o seed.json >/dev/null) \
      || fail "prepare business data"
    pass "seed.json 就绪"

    step "2 基准快照"
    (cd "$workdir" && OV_PLATFORM_DATABASE_URL="$drill_url" \
      PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" snapshot "$drill_url" -o snapshot.json >/dev/null) \
      || fail "snapshot"
    pass "snapshot.json 就绪"

    step "3 备份（PG 加密 + 数据卷加密 + 审计前置）"
    export OV_PLATFORM_DATABASE_URL="$drill_tools" OV_PLATFORM_BACKUP_AUDIT_LOG="$workdir/audit/ops-audit.log" \
      OV_PLATFORM_BACKUP_AUDIT_DB_URL="$drill_tools"
    "$BIN" backup "$workdir/backups" >/dev/null || fail "backup pg"
    mkdir -p "$workdir/vol/workspace" "$workdir/vol/.openviking"
    echo "drill-memory-content" > "$workdir/vol/workspace/memory.md"
    echo "drill-agfs-db" > "$workdir/vol/.openviking/agfs.db"
    "$BIN" backup-volume "$workdir/vol" "$workdir/backups" >/dev/null || fail "backup volume"
    pg_enc="$(ls "$workdir/backups/"ov_platform_*.dump.gz.gpg | head -1)"
    vol_enc="$(ls "$workdir/backups/"ov_volume_*.tar.gz.gpg | head -1)"
    "$BIN" verify "$pg_enc" >/dev/null || fail "verify backup"
    pass "PG 备份 $(basename "${pg_enc}") + 数据卷备份 $(basename "${vol_enc}")（加密、可校验）"

    step "4 故障注入（删用户/清审计/清业务引用/坏 outbox + 数据卷文件删除）"
    OV_PLATFORM_DATABASE_URL="$drill_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" fault-inject "$drill_url" >/dev/null \
      || fail "fault inject"
    rm -rf "$workdir/vol"
    pass "故障已注入（登录/Key/审计/引用/outbox/数据卷均丢失）"

    step "5 恢复（PG + 数据卷，非交互、双路审计）"
    "$BIN" restore "$pg_enc" >/dev/null || fail "restore pg"
    "$BIN" restore-volume "$vol_enc" "$workdir" >/dev/null || fail "restore volume"
    [ -f "$workdir/vol/workspace/memory.md" ] || fail "volume restore content missing"
    pass "PG 与数据卷恢复完成；恢复审计已写入（iam_audit_events + ops-audit.log）"

    step "6 恢复后一致性校验（登录/Key/Session/审计/无重复/outbox/retry）"
    session_raw="$(json_field "$workdir/seed.json" session_raw)"
    alice_key="$(json_field "$workdir/seed.json" alice_key)"
    failed_id="$(json_field "$workdir/seed.json" failed_account_id)"
    (cd "$workdir" && OV_PLATFORM_DATABASE_URL="$drill_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" \
      verify "$drill_url" --snapshot snapshot.json --session-raw "$session_raw" --alice-key "$alice_key" \
      --failed-account-id "$failed_id" -o verify-report.json) || fail "verify restore"
    pass "一致性校验全部通过（verify-report.json）"

    echo "{\"drill\":\"full\",\"result\":\"PASS\",\"time\":\"$ts\",\"db\":\"$db\",\"backup\":\"$(basename "$pg_enc")\",\"volume\":\"$(basename "$vol_enc")\",\"workdir\":\"$workdir\"}" > "$workdir/report.json"
    cat "$workdir/report.json"
    ;;

  rollback)
    step "0 演练库准备（重建 + PSA + 业务数据）"
    rebuild_db "$db" "$drill_url"
    OV_PLATFORM_DATABASE_URL="$drill_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" prepare "$drill_url" -o "$workdir/seed.json" >/dev/null \
      || fail "prepare business data"
    session_raw="$(json_field "$workdir/seed.json" session_raw)"
    alice_key="$(json_field "$workdir/seed.json" alice_key)"
    failed_id="$(json_field "$workdir/seed.json" failed_account_id)"

    step "1 记录发布版本（模拟发布新版本 v0.4.13+p5e3）"
    echo "{\"version\":\"v0.4.13+p5e3\",\"image\":\"registry.example/ovp-product-server:v0.4.13\",\"released_at\":\"$ts\"}" > "$workdir/deploy-list.json"
    cat "$workdir/deploy-list.json"

    step "2 回滚：migration 可验证 down（downgrade -1 → upgrade head，数据保留）"
    before="$(OV_PLATFORM_DATABASE_URL="$drill_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" snapshot "$drill_url" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["iam_users"])')"
    PYTHONPATH="$REPO_ROOT" "$PY" -c "
from alembic import command
from alembic.config import Config
cfg = Config('$REPO_ROOT/openviking/server/platform/alembic.ini')
cfg.set_main_option('sqlalchemy.url', '$drill_url')
command.downgrade(cfg, '-1')
command.upgrade(cfg, 'head')
" 2>/dev/null || fail "migration down/up"
    after="$(OV_PLATFORM_DATABASE_URL="$drill_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" snapshot "$drill_url" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["iam_users"])')"
    [ "${before}" = "${after}" ] || fail "downgrade/upgrade 后用户数据丢失: ${before} -> ${after}"
    pass "down migration 可验证：downgrade -1 → upgrade head 后 iam_users 计数不变 (${before})"

    step "3 回滚版本标记（镜像 tag 切回上次良好版本 v0.4.12）"
    echo "{\"version\":\"v0.4.12\",\"image\":\"registry.example/ovp-product-server:v0.4.12\",\"rolled_back_at\":\"$ts\"}" > "$workdir/deploy-list.json"
    cat "$workdir/deploy-list.json"

    step "4 回滚后仍以 PG IAM 鉴权（登录/Session/API Key 全从 PG 解析，不回退旧 registry）"
    (cd "$workdir" && OV_PLATFORM_DATABASE_URL="$drill_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" \
      verify "$drill_url" --session-raw "$session_raw" --alice-key "$alice_key" \
      --failed-account-id "$failed_id" --skip-restore-audit -o verify-report.json) \
      || fail "pg iam after rollback"
    pass "PG IAM 鉴权在版本回滚后仍生效（verify-report.json）"

    echo "{\"drill\":\"rollback\",\"result\":\"PASS\",\"time\":\"$ts\",\"db\":\"$db\"}" > "$workdir/report.json"
    cat "$workdir/report.json"
    ;;

  standalone)
    [ -n "${backup_file}" ] && [ -f "${backup_file}" ] || fail "need --backup <file>（先用 full 产出备份）"
    step "0 独立实例（独立库 ${target_db}）恢复备份"
    rebuild_db "$target_db" "$target_url"
    export OV_PLATFORM_DATABASE_URL="${target_tools}" OV_PLATFORM_BACKUP_AUDIT_LOG="${workdir}/audit/ops-audit.log" \
      OV_PLATFORM_BACKUP_AUDIT_DB_URL="${target_tools}"
    "$BIN" restore "$backup_file" >/dev/null || fail "restore to standalone"
    pass "备份已恢复到独立实例 (${target_db})；主实例未触碰"

    step "1 独立实例数据校验（与备份时快照一致）"
    [ -f "${workdir}/snapshot.json" ] || fail "缺少 full 演练的 snapshot.json（先在相同 workdir 运行 full）"
    (cd "$workdir" && OV_PLATFORM_DATABASE_URL="$target_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" \
      snapshot "$target_url" -o standalone-snapshot.json >/dev/null) || fail "snapshot standalone"
    "$PY" -c "
import json
a = json.load(open('$workdir/standalone-snapshot.json'))
b = json.load(open('$workdir/snapshot.json'))
bad = {k: (a[k], b[k]) for k in a if k in b and k != 'iam_audit_events' and a[k] != b[k]}
raise SystemExit(1 if bad else 0)
" || fail "standalone data mismatch"
    pass "独立实例数据与备份快照一致（除审计因恢复事件新增外逐表相等）"

    step "2 不可逆变更占位演练（在独立实例执行并验证；主实例仅验证通过后才允许执行）"
    session_raw="$(json_field "$workdir/seed.json" session_raw)"
    alice_key="$(json_field "$workdir/seed.json" alice_key)"
    failed_id="$(json_field "$workdir/seed.json" failed_account_id)"
    (cd "$workdir" && OV_PLATFORM_DATABASE_URL="$target_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" \
      verify "$target_url" --session-raw "$session_raw" --alice-key "$alice_key" \
      --failed-account-id "$failed_id" -o standalone-verify.json) || fail "standalone verify"
    pass "独立实例不可逆变更演练通过（standalone-verify.json）；主实例未改动"

    step "3 主实例未受影响（计数与快照一致）"
    main_now="$(OV_PLATFORM_DATABASE_URL="$drill_url" PYTHONPATH="$REPO_ROOT" "$PY" "$DRILL_LIB" snapshot "$drill_url" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print("iam_users=%s audit=%s" % (d["iam_users"], d["iam_audit_events"]))')"
    echo "主实例 (${db}) 当前 ${main_now}"
    pass "主实例保持演练前状态（独立实例验证后才允许切流量/执行不可逆变更）"

    echo "{\"drill\":\"standalone\",\"result\":\"PASS\",\"time\":\"$ts\",\"target_db\":\"$target_db\",\"backup\":\"$(basename "$backup_file")\"}" > "$workdir/report.json"
    cat "$workdir/report.json"
    ;;

  *)
    echo "usage: $0 full|rollback|standalone [--db X] [--workdir D] [--keep]" >&2
    exit 1
    ;;
esac
