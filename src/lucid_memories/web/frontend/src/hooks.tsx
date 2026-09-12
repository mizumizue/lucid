import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DependencyList,
  type PropsWithChildren,
} from "react";
import { useSearchParams } from "react-router-dom";

export type ResourceState<T> = {
  data: T | null;
  loading: boolean;
  error: Error | null;
  retry: () => void;
};

export function useResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  dependencies: DependencyList,
): ResourceState<T> {
  const [state, setState] = useState<Omit<ResourceState<T>, "retry">>({
    data: null,
    loading: true,
    error: null,
  });
  const [revision, setRevision] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const controller = new AbortController();
    setState((current) => ({ ...current, loading: true, error: null }));
    loaderRef.current(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState((current) => ({
            ...current,
            loading: false,
            error: error instanceof Error ? error : new Error("Unknown error"),
          }));
        }
      });
    return () => controller.abort();
    // The caller owns the dependency list; the loader is intentionally kept in a ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, revision]);

  const retry = useCallback(() => setRevision((value) => value + 1), []);
  return useMemo(() => ({ ...state, retry }), [state, retry]);
}

export function useDebouncedValue<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function usePageQuery() {
  const [searchParams, setSearchParams] = useSearchParams();
  const setValue = (key: string, value: string | number | undefined, resetPage = true) => {
    const next = new URLSearchParams(searchParams);
    if (value === undefined || value === "") next.delete(key);
    else next.set(key, String(value));
    if (resetPage && key !== "page") next.delete("page");
    setSearchParams(next);
  };
  const replaceQuery = (values: Record<string, string>) => {
    const next = new URLSearchParams();
    Object.entries(values).forEach(([key, value]) => {
      if (value) next.set(key, value);
    });
    setSearchParams(next);
  };
  return { searchParams, setValue, replaceQuery };
}

type RefreshContextValue = {
  revision: number;
  autoRefresh: boolean;
  setAutoRefresh: (enabled: boolean) => void;
  refresh: () => void;
};

const RefreshContext = createContext<RefreshContextValue | null>(null);

export function RefreshProvider({ children }: PropsWithChildren) {
  const [revision, setRevision] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(false);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = window.setInterval(() => setRevision((value) => value + 1), 30_000);
    return () => window.clearInterval(interval);
  }, [autoRefresh]);

  const value = useMemo(
    () => ({
      revision,
      autoRefresh,
      setAutoRefresh,
      refresh: () => setRevision((current) => current + 1),
    }),
    [revision, autoRefresh],
  );
  return <RefreshContext.Provider value={value}>{children}</RefreshContext.Provider>;
}

export function useRefresh(): RefreshContextValue {
  const value = useContext(RefreshContext);
  if (!value) throw new Error("useRefresh must be used inside RefreshProvider");
  return value;
}

