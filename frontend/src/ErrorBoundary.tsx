import { Component, type ErrorInfo, type ReactNode } from "react";

/** A crash in one screen shows a message and a way back, never a blank page. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Screen crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="phone">
        <main className="content">
          <h1>Something went wrong on this screen</h1>
          <p className="muted small mono">{this.state.error.message}</p>
          <div className="row">
            <button className="btn primary" onClick={() => window.location.reload()}>Reload</button>
            <a className="btn" href="/">Start page</a>
          </div>
        </main>
      </div>
    );
  }
}
