"use client";

import { useEffect, useReducer, useState } from "react";
import { api, streamUrl } from "@/lib/api";
import { initialState, reducer } from "@/lib/reducer";
import type { BusEvent } from "@/lib/types";

const RETRY_MS = 3000;

/**
 * Boot order: snapshot → replay the active (or latest) incident's stored events → open the
 * stream from the snapshot's last event ID. Nothing is missed and nothing is applied twice
 * (the reducer drops event IDs it has already seen). Any disconnect runs the same boot again
 * rather than trusting EventSource's silent retries: a backend that restarted may have lost
 * the in-flight run, and only a fresh snapshot tells the truth. While the backend is down,
 * the page shows the designed offline state and retries every few seconds.
 */
export function useMemoryOps() {
  const [state, dispatch] = useReducer(reducer, initialState);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const scheduleBoot = () => {
      if (cancelled || retry) return;
      retry = setTimeout(() => {
        retry = null;
        void boot();
      }, RETRY_MS);
    };

    async function boot() {
      source?.close();
      source = null;
      try {
        const snap = await api.state();
        let replayId = snap.active_incident?.id;
        if (!replayId) replayId = (await api.incidents()).at(-1)?.id;
        const events = replayId
          ? (await api.incident(replayId)).events.filter((e) => e.id <= snap.last_event_id)
          : [];
        if (cancelled) return;
        // Applied together, so React renders once: no flash of an empty rail on reconnect.
        dispatch({ type: "resync" });
        dispatch({ type: "replay", events });
        dispatch({ type: "snapshot", state: snap });
        api
          .history()
          .then((data) => !cancelled && dispatch({ type: "history", data }))
          .catch(() => undefined); // sparklines simply fill up live

        source = new EventSource(streamUrl(snap.last_event_id));
        source.onopen = () => dispatch({ type: "connection", status: "live" });
        source.onmessage = (msg) => {
          try {
            dispatch({ type: "event", event: JSON.parse(msg.data) as BusEvent });
          } catch {
            // a malformed frame is dropped, never fatal
          }
        };
        source.onerror = () => {
          source?.close();
          dispatch({ type: "connection", status: "reconnecting" });
          scheduleBoot();
        };
      } catch {
        if (!cancelled) {
          dispatch({ type: "connection", status: "offline" });
          scheduleBoot();
        }
      }
    }

    void boot();
    return () => {
      cancelled = true;
      source?.close();
      if (retry) clearTimeout(retry);
    };
  }, []);

  return [state, dispatch] as const;
}

/** Re-renders every `ms` while `active` — for countdowns only, never for the plant. */
export function useNow(active: boolean, ms = 250): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [active, ms]);
  return now;
}

/** A value from the API that is refetched whenever `version` changes (and every `pollMs`). */
export function useApi<T>(load: () => Promise<T>, version: number, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const run = () =>
      load()
        .then((d) => {
          if (!cancelled) {
            setData(d);
            setError(null);
          }
        })
        .catch((e: Error) => !cancelled && setError(e.message));
    void run();
    const id = pollMs ? setInterval(run, pollMs) : null;
    return () => {
      cancelled = true;
      if (id) clearInterval(id);
    };
    // `load` is a stable module-level function in every caller
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, pollMs]);
  return { data, error };
}
