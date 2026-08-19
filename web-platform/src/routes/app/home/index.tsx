import PlaceholderPage from "@/components/PlaceholderPage";

export default function HomePage() {
  return (
    <PlaceholderPage
      title="首页"
      plannedIn="P3-E3"
      description="内容数量、最近 Session/Resource/Skill、失败摘要；不展示 Queue/锁/模型/VectorDB 等底层状态（AC①）。"
    />
  );
}
