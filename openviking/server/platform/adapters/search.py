"""真实语义检索适配器（11 §69/§70，P5-E1 接线）。

`SearchEngine` 协议（providers/session/backend.py）的真实实现：对接
`SearchService.find/search`（openviking/service/search_service.py）。

- `find` → `service.search.find(query, ctx, target_uri=roots)`（不加载 Session）；
- `search` → `service.search.search(query, ctx, target_uri=roots)`；
- roots 由产品服务端固定传入（User 私有根 + Account 共享根，11 §69.2），
  OpenViking 侧再做一次 RequestContext 隔离（namespace.is_accessible）；
- 返回 `RawHit` 携带内部字段（uri/score/level/provenance/relations/category），
  产品 DTO 层统一脱敏（AC③，客户端永远看不到内部字段）；
- `display_name` 由 URI 叶子名派生（产品展示名映射）。
"""

from __future__ import annotations

from typing import Optional

from openviking.server.platform.adapters.runtime import (
    ServiceProvider,
    default_service_provider,
    require_service,
    user_ctx,
)
from openviking.server.platform.session.backend import RawHit

RAW_HIT_LEVEL_BY_KIND = {"memory": 0, "resource": 1, "skill": 2}


def _leaf_name(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]


class RealSearchEngine:
    """真实 OpenViking 检索执行面（SearchService，惰性运行时解析）。"""

    def __init__(self, service_provider: Optional[ServiceProvider] = None) -> None:
        self._provider: ServiceProvider = service_provider or default_service_provider()

    def _search_service(self):
        service = require_service(self._provider, "search_engine")
        search = getattr(service, "search", None)
        if search is None:
            from openviking.server.platform.adapters.runtime import AdapterRuntimeUnavailable

            raise AdapterRuntimeUnavailable("search_engine")
        return service, search

    async def find(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        roots: list[str],
        query: str,
        context_type: str | None,
        tags: list[str],
        since: str | None,
        until: str | None,
    ) -> list[RawHit]:
        _, search = self._search_service()
        ctx = user_ctx(account_ov_id, user_ov_id)
        filter_expr = self._build_filter(
            context_type=context_type, tags=tags, since=since, until=until
        )
        result = await search.find(query=query, ctx=ctx, target_uri=list(roots), filter=filter_expr)
        return self._to_raw_hits(result)

    async def search(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        roots: list[str],
        query: str,
        context_type: str | None,
        tags: list[str],
        since: str | None,
        until: str | None,
        session_ov_id: str,
    ) -> list[RawHit]:
        service, search = self._search_service()
        ctx = user_ctx(account_ov_id, user_ov_id)
        filter_expr = self._build_filter(
            context_type=context_type, tags=tags, since=since, until=until
        )
        session = None
        if session_ov_id:
            sessions = getattr(service, "sessions", None)
            if sessions is not None:
                try:
                    session = sessions.session(ctx, session_ov_id)
                    await session.load()
                except Exception:
                    session = None
        result = await search.search(
            query=query, ctx=ctx, target_uri=list(roots), session=session, filter=filter_expr
        )
        return self._to_raw_hits(result)

    def _build_filter(self, *, context_type, tags, since, until) -> dict | None:
        from openviking.storage.expr import And, Contains, Eq, TimeRange

        conds: list = []
        if context_type:
            conds.append(Eq(field="context_type", value=context_type))
        for tag in tags:
            conds.append(Contains(field="tags", substring=tag))
        if since or until:
            conds.append(TimeRange(field="updated_at", start=since or None, end=until or None))
        if not conds:
            return None
        return And(conds=conds) if len(conds) > 1 else conds[0]

    def _to_raw_hits(self, result) -> list[RawHit]:
        hits: list[RawHit] = []
        if result is None:
            return hits
        for item in getattr(result, "memories", []) or []:
            hits.append(self._to_raw_hit(item, "memory"))
        for item in getattr(result, "resources", []) or []:
            hits.append(self._to_raw_hit(item, "resource"))
        for item in getattr(result, "skills", []) or []:
            hits.append(self._to_raw_hit(item, "skill"))
        return hits

    def _to_raw_hit(self, item, context_type: str) -> RawHit:
        uri = item.uri
        relations = [
            {"type": rel.rel_type if hasattr(rel, "rel_type") else "related", "uri": rel.uri}
            for rel in getattr(item, "relations", []) or []
        ]
        return RawHit(
            uri=uri,
            context_type=context_type,
            display_name=_leaf_name(uri) or item.abstract or uri,
            abstract=item.abstract or "",
            match_reason=item.match_reason or "",
            score=float(getattr(item, "score", 0.0) or 0.0),
            level=int(getattr(item, "level", RAW_HIT_LEVEL_BY_KIND.get(context_type, 1)) or 1),
            query_plan=None,
            provenance=[{"source": "viking_fs"}],
            relations=relations,
            category=getattr(item, "category", "") or "",
            memory_type=None,
        )
