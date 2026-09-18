import { Alert, Button, Card, Space, Typography } from "antd";
import { startOidcLogin } from "../auth/oidc";
import type { BrowserAuthConfig } from "../auth/browserSession";

export function LoginPage({ config, error, returnTo, onRetry }: { config?: BrowserAuthConfig; error?: string; returnTo?: string; onRetry?: () => void }) {
  return <Card className="login-card"><Typography.Title level={2}>登录基础设施工作台</Typography.Title>
    <Typography.Paragraph>登录后进入个人或团队共享空间。已登录统一账号的用户可直接继续访问。</Typography.Paragraph>
    {error && <Alert showIcon type="error" title="暂时无法进入" description={error} style={{ marginBottom: 20 }} />}
    {!config?.sso_enabled && <Alert showIcon type="info" title={config?.demo_mode ? "本机演示环境" : "尚未配置统一登录"} description="正式接入需要管理员配置身份提供方，并关联已有账号。" style={{ marginBottom: 20 }} />}
    <Space wrap><Button type="primary" disabled={!config?.sso_enabled} onClick={() => void startOidcLogin(returnTo)}>使用统一账号登录</Button>{onRetry && <Button onClick={onRetry}>{config?.demo_mode ? "进入演示工作台" : "重新检查登录状态"}</Button>}<Button href="/app-next/">返回工作台</Button></Space>
  </Card>;
}
