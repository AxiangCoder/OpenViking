import { Navigate } from "@tanstack/react-router";

export default function AdminIndexPage() {
  return <Navigate to="/admin/users" replace />;
}
