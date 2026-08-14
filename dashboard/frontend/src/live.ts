import { useEffect, useReducer, useRef, useState } from "react";
import type { LiveEvent, LiveMessage, LiveState } from "./types";

type ConnectionState = "connecting" | "connected" | "disconnected";

export type Store = {
  revision: number;
  state: LiveState | null;
  events: LiveEvent[];
};

export function reduceMessage(store: Store, message: LiveMessage): Store {
  if (message.kind === "snapshot") {
    return { revision: message.revision, state: message.state, events: message.events };
  }
  if (message.kind === "event") {
    return { ...store, events: [...store.events, message.event].slice(-500) };
  }
  if (!store.state) {
    return store;
  }
  return {
    ...store,
    revision: message.revision,
    state: deepMerge(store.state, message.changes),
  };
}

export function useLiveRuntime() {
  const [store, dispatch] = useReducer(reduceMessage, { revision: 0, state: null, events: [] });
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const reconnectTimer = useRef<number | null>(null);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let retryMs = 750;

    const connect = () => {
      if (disposed) return;
      setConnection("connecting");
      const configured = import.meta.env.VITE_DASHBOARD_WS as string | undefined;
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const url = configured || `${protocol}://${window.location.host}/ws/live`;
      socket = new WebSocket(url);
      socket.onopen = () => {
        retryMs = 750;
        setConnection("connected");
      };
      socket.onmessage = (event) => {
        try {
          dispatch(JSON.parse(event.data) as LiveMessage);
        } catch {
          // Ignore malformed transport data; a later snapshot repairs the view.
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (disposed) return;
        setConnection("disconnected");
        reconnectTimer.current = window.setTimeout(connect, retryMs);
        retryMs = Math.min(5000, Math.round(retryMs * 1.6));
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current);
      socket?.close();
    };
  }, []);

  return { ...store, connection };
}

function deepMerge<T>(target: T, update: Partial<T>): T {
  const output = { ...(target as Record<string, unknown>) };
  for (const [key, value] of Object.entries(update as Record<string, unknown>)) {
    const existing = output[key];
    if (isObject(existing) && isObject(value)) {
      output[key] = deepMerge(existing, value);
    } else {
      output[key] = value;
    }
  }
  return output as T;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
