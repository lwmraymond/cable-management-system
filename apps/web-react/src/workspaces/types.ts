export type WorkspaceKind = "personal" | "shared";
export type WorkspaceScope = { project_id: string | null; location_id: string | null };
export type Workspace = {
  id: string;
  name: string;
  kind: WorkspaceKind;
  role: string;
  can_manage: boolean;
  is_owner?: boolean;
  can_leave?: boolean;
  member_count?: number | null;
  permissions?: string[];
  access_revision?: string;
  project_id?: string | null;
  location_id?: string | null;
  scopes?: WorkspaceScope[];
};
export type BrowserSession = {
  user: { id: string; display_name: string; email: string };
  auth_method: "bearer" | "cookie" | "demo";
  workspaces: Workspace[];
  csrf_header_name: string;
  csrf_cookie_name: string;
  can_create_workspaces: boolean;
};
export type WorkspaceMember = {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  active: boolean;
  is_owner: boolean;
  version?: number;
  has_scoped_access?: boolean;
};
