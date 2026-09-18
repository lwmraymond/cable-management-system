import { clearAccessToken } from "./tokenStore";
import { safeReturnTo } from "./browserSession";

// The API owns PKCE, state/nonce validation and the HttpOnly session cookie.
export async function startOidcLogin(returnTo = "/app-next/"): Promise<void> {
  clearAccessToken();
  window.location.assign(`/api/v1/auth/login?${new URLSearchParams({ return_to: safeReturnTo(returnTo) })}`);
}
