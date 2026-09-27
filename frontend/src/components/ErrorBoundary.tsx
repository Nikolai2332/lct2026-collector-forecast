import { Button, Result } from 'antd';
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface State {
  error: Error | null;
}

/** Ошибка отрисовки экрана (или не догрузился чанк после обновления) — вместо белого экрана предлагаем повторить */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.warn('Ошибка экрана', error, info.componentStack);
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    const chunk = /dynamically imported module|Loading chunk/i.test(this.state.error.message);
    return (
      <Result
        status="warning"
        title={chunk ? 'Приложение обновилось' : 'Экран не удалось показать'}
        subTitle={chunk ? 'Обновите страницу, чтобы загрузить новую версию.' : this.state.error.message}
        extra={
          <Button type="primary" onClick={() => (chunk ? window.location.reload() : this.setState({ error: null }))}>
            {chunk ? 'Обновить страницу' : 'Повторить'}
          </Button>
        }
      />
    );
  }
}
