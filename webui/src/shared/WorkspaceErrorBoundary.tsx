import { Component, type ReactNode } from "react";


interface Props {
  children: ReactNode;
  title: string;
}


export class WorkspaceErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <section role="alert">
      <h1>{this.props.title} 暂时不可用</h1>
      <p>当前工作区加载失败，其他 Agent 不受影响。</p>
      <button type="button" onClick={() => this.setState({ failed: false })}>重试</button>
    </section>;
  }
}
