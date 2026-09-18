import { Component, type PropsWithChildren } from "react";

/** Keep navigation usable when a page or a deferred module fails to render. */
export class PageErrorBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  render() {
    if (!this.state.failed) return this.props.children;
    return <section className="page-recovery workspace-panel" role="alert" aria-labelledby="page-recovery-title">
      <h1 id="page-recovery-title">页面暂时无法显示</h1>
      <p>页面加载或运行时出现问题。请重新加载，或返回工作台继续使用。</p>
      <p>未保存的编辑可能无法保留；重新加载不会自动提交操作。</p>
      <div className="page-recovery-actions">
        <button type="button" onClick={() => window.location.reload()}>重新加载页面</button>
        <a href="/app-next/">返回工作台</a>
      </div>
    </section>;
  }
}
