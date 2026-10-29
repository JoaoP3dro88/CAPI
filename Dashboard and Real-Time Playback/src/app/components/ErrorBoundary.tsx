import React from 'react';

interface ErrorBoundaryProps {
  fallback?: React.ReactNode;
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error('UI error:', error);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="bg-white border border-[#BFC0C2] p-6">
            <h2 className="text-sm font-bold uppercase tracking-wide text-[#000000]">UI Error</h2>
            <p className="text-sm text-[#525F6B] mt-2">{this.state.error?.message ?? 'Unknown error'}</p>
          </div>
        )
      );
    }

    return this.props.children;
  }
}
