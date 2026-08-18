/**
 * 404 页：未匹配路由统一回落到本页（AC⑥ 非法/未知 URL 语义）。
 */

export default function NotFoundPage() {
  return (
    <div className="error-page" data-testid="not-found-page">
      <h2>未找到页面（404）</h2>
      <p className="error-page-message">请求的路由不存在，或对应的产品对象不可访问。</p>
      <p className="error-page-hint">
        请检查地址是否正确；如果是从书签/搜索结果进入，对象可能已被删除或不在当前范围内。
      </p>
    </div>
  );
}
