/**
 * 占位页工厂：路由树全量展开（06 §13.2）用。
 * 详情页占位会读取产品 ID 参数并校验（AC⑥：URL 无 Viking URI/Account/User ID）。
 */

import { useParams } from "@tanstack/react-router";
import PlaceholderPage from "@/components/PlaceholderPage";
import { isProductId } from "@/lib/links";

export interface PlaceholderSpec {
  title: string;
  plannedIn: string;
  description?: string;
}

export function placeholder(spec: PlaceholderSpec) {
  return function Placeholder() {
    return (
      <PlaceholderPage
        title={spec.title}
        plannedIn={spec.plannedIn}
        description={spec.description}
      />
    );
  };
}

export interface ProductDetailSpec {
  kind: "Resource" | "Skill";
  plannedIn: string;
}

/** 详情页占位：展示产品 ID 参数（非内部标识符），非法 ID 显示 404 语义。 */
export function productDetailPlaceholder(spec: ProductDetailSpec) {
  return function ProductDetailPlaceholder() {
    const params = useParams({ strict: false }) as Record<string, string>;
    const productId = params["productId"] ?? params["resourceId"] ?? params["skillId"] ?? "";
    if (!isProductId(productId)) {
      return (
        <PlaceholderPage
          title={`${spec.kind} 详情`}
          plannedIn={spec.plannedIn}
          description="地址中的产品 ID 无效（404 语义）。URL 只接受产品 ID（UUID），不接受 Viking URI / Account / User 标识符（AC⑥）。"
        />
      );
    }
    return (
      <PlaceholderPage
        title={`${spec.kind} 详情`}
        plannedIn={spec.plannedIn}
        description={`产品 ID：${productId}（跳转参数契约由 P3-E1 冻结，详见 lib/links.ts）`}
      />
    );
  };
}
