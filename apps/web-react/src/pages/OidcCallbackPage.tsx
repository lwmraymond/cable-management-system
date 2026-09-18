import { Alert, Button } from "antd";
import { Link } from "react-router-dom";
export function OidcCallbackPage() {
  return <div className="login-card"><Alert type="warning" showIcon title="登录回调地址已更新" description="请重新从统一登录入口进入。身份提供方回调地址应配置为 /api/v1/auth/callback。" /><Link to="/login"><Button>返回登录</Button></Link></div>;
}
