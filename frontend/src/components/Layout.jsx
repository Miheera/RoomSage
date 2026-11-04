import { Suspense, useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { USE_MOCK } from "../lib/config";

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#17302B" />
      <path d="M7 11l9-5 9 5v10l-9 5-9-5z" fill="none" stroke="#8FB39B" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M7 11l9 5 9-5M16 16v10" fill="none" stroke="#F2F4F0" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

export default function Layout() {
  const [online, setOnline] = useState(null);

  useEffect(() => {
    if (USE_MOCK) return undefined;
    let stop = false;
    const ping = async () => {
      try {
        await api.health();
        if (!stop) setOnline(true);
      } catch {
        if (!stop) setOnline(false);
      }
    };
    ping();
    const t = setInterval(ping, 20000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);

  return (
    <>
      <header className="top">
        <Link to="/" className="brand">
          <Mark /> RoomSage
        </Link>
        <span className={`status ${USE_MOCK ? "status--demo" : online === false ? "status--off" : online ? "status--on" : ""}`} role="status">
          <i />
          {USE_MOCK ? "Demo mode" : online === false ? "Server offline" : online ? "Server connected" : "Connecting"}
        </span>
      </header>
      <Suspense fallback={<p className="page muted">Loading…</p>}>
        <Outlet />
      </Suspense>
    </>
  );
}
