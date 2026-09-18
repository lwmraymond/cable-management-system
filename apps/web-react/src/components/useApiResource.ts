import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

type ResourceState<T> = { scope: object; data?: T; error?: Error; loading: boolean };

/**
 * Dependencies identify the complete resource/context; include every scope input.
 * Refreshes keep same-scope data, but a dependency change hides data and errors
 * immediately, including the render before effects can cancel the old request.
 */
export function useApiResource<T>(loader: (signal?: AbortSignal) => Promise<T>, dependencies: readonly unknown[] = []) {
  const scope = useMemo(() => ({}), dependencies); // eslint-disable-line react-hooks/exhaustive-deps
  const [state, setState] = useState<ResourceState<T>>(() => ({ scope, loading: true }));
  const committed = useRef<{ scope: object; loader: typeof loader } | null>(null);
  const pending = useRef<AbortController | null>(null);

  // Publish only committed renders: a suspended/abandoned render must not cancel
  // the visible resource or replace the loader used by its refresh button.
  useLayoutEffect(() => { committed.current = { scope, loader }; });
  useLayoutEffect(() => () => {
    committed.current = null;
    const previous = pending.current;
    pending.current = null;
    previous?.abort();
  }, [scope]);

  const reload = useCallback(async () => {
    const current = committed.current;
    if (!current || current.scope !== scope) return;

    const previous = pending.current;
    const controller = new AbortController();
    pending.current = controller;
    previous?.abort();
    setState(value => ({ scope, data: value.scope === scope ? value.data : undefined, loading: true }));
    const isCurrent = () => committed.current?.scope === scope && pending.current === controller && !controller.signal.aborted;
    try {
      const data = await current.loader(controller.signal);
      if (isCurrent()) setState({ scope, data, loading: false });
    } catch (caught) {
      if (isCurrent()) {
        const error = caught instanceof Error ? caught : new Error(String(caught));
        setState(value => ({ scope, data: value.scope === scope ? value.data : undefined, error, loading: false }));
      }
    } finally {
      if (pending.current === controller) pending.current = null;
    }
  }, [scope]);

  useEffect(() => { void reload(); }, [reload]);
  return {
    data: state.scope === scope ? state.data : undefined,
    error: state.scope === scope ? state.error : undefined,
    loading: state.scope === scope ? state.loading : true,
    reload,
  };
}
