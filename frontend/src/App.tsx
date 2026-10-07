import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, useLiveData } from "./useLiveData";
import { BROKER_LABEL, type Account, type Opportunity, type Order, type QuoteRow, type Risk, type Snapshot, type Trade } from "./types";

const inr = (n: number, d = 2) => "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (n: number) => (n > 0 ? "+" : n < 0 ? "−" : "") + inr(Math.abs(n));
const pnlColor = (n: number) => (n > 0 ? "text-emerald-400" : n < 0 ? "text-rose-400" : "text-slate-300");
const time = (ms: number) => new Date(ms).toLocaleTimeString("en-IN", { hour12: false });

export default function App() {
  const { data, connected } = useLiveData();
  return (
    <div className="min-h-full">
      <div className="bg-amber-500 text-amber-950 text-center text-xs sm:text-sm font-bold tracking-wide py-1.5">
        ⚠ FULLY SIMULATED — SIMULATED MARKET FEED, BROKER ACCOUNTS &amp; ORDERS · NO REAL MONEY
      </div>
      <main className="mx-auto max-w-[1500px] p-3 sm:p-5 space-y-4">
        <Header data={data} connected={connected} />
        {!data ? <Loading connected={connected} /> : <Dashboard d={data} />}
        <footer className="pt-2 pb-6 text-center text-xs text-slate-500">
          Educational project · Simulated NSE/BSE market, simulated broker accounts and orders · Not connected to any real broker or exchange · Costs are approximate
        </footer>
      </main>
    </div>
  );
}

function Loading({ connected }: { connected: boolean }) {
  return <div className="card text-center py-20 text-slate-400">{connected ? "Waiting for market data…" : "Connecting to engine…"}</div>;
}

function Header({ data, connected }: { data: Snapshot | null; connected: boolean }) {
  const s = data?.status;
  const [busy, setBusy] = useState(false);
  const act = async (path: string) => { setBusy(true); try { await api(path, "POST"); } finally { setBusy(false); } };
  return (
    <header className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
      <div>
        <h1 className="text-xl sm:text-2xl font-extrabold tracking-tight text-white">
          Multi-Broker Arbitrage <span className="text-cyan-400">Simulator</span>
        </h1>
        <p className="text-sm text-slate-400">Real-time NSE ↔ BSE arbitrage detection &amp; execution · two simulated broker accounts</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-violet-500/15 px-3 py-1 text-xs font-bold text-violet-300">
          MODE: SIMULATION
        </span>
        <span className={`rounded-full px-3 py-1 text-xs font-bold ${connected ? "bg-cyan-500/15 text-cyan-300" : "bg-rose-500/15 text-rose-300"}`}>
          {connected ? "● DASHBOARD CONNECTED" : "○ RECONNECTING"}
        </span>
        {s && (<>
          <button disabled={busy} onClick={() => act(s.engine === "RUNNING" ? "/api/engine/stop" : "/api/engine/start")}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold hover:bg-slate-800">
            {s.engine === "RUNNING" ? "⏸ Stop engine" : "▶ Start engine"}
          </button>
          <button disabled={busy} onClick={() => act(s.paper_trading === "ENABLED" ? "/api/trading/disable" : "/api/trading/enable")}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold hover:bg-slate-800">
            {s.paper_trading === "ENABLED" ? "Disable auto-trading" : "Enable auto-trading"}
          </button>
          <button disabled={busy} onClick={() => confirm("Reset simulated accounts, orders and trade history?") && act("/api/trading/reset")}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-rose-300 hover:bg-slate-800">Reset</button>
        </>)}
      </div>
    </header>
  );
}

function Dashboard({ d }: { d: Snapshot }) {
  return (
    <>
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <StatusPanel d={d} />
        <Kpis d={d} />
      </div>
      <PriceTable rows={d.quotes} />
      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Opportunities items={d.opportunities} stats={d.detector} />
        <PnlChart d={d} />
      </div>
      <Accounts accounts={d.portfolio.accounts} />
      <Trades trades={d.trades} />
      <Orders orders={d.orders} stats={d.order_stats} />
      <div className="grid gap-4 lg:grid-cols-3">
        <RiskPanel risk={d.risk} />
        <SymbolChart d={d} />
        <Rejections d={d} />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ status */
function Dot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  const c = ok ? "bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400" : warn ? "bg-amber-400" : "bg-rose-500";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${c}`} />;
}

function StatusPanel({ d }: { d: Snapshot }) {
  const s = d.status;
  const line = (label: string, value: string, ok: boolean, warn = false) => (
    <div className="flex items-center justify-between py-1.5">
      <span className="flex items-center gap-2"><Dot ok={ok} warn={warn} />{label}</span>
      <span className={`font-mono text-xs font-semibold ${ok ? "text-emerald-300" : warn ? "text-amber-300" : "text-rose-300"}`}>{value}</span>
    </div>
  );
  return (
    <section className="card">
      <h2 className="card-title">System status</h2>
      {s.feeds.map((f) => (
        <div key={f.broker} className="border-b border-slate-800 pb-1 mb-1">
          {line(f.name, f.state.toUpperCase(), f.state === "connected", f.state === "reconnecting" || f.state === "connecting")}
          <div className="pl-[18px] text-[11px] text-slate-500 num">
            p50 {f.latency_p50_ms ?? "–"}ms · p95 {f.latency_p95_ms ?? "–"}ms · {f.messages.toLocaleString()} msgs · {f.reconnects} reconnects
          </div>
        </div>
      ))}
      {line("Simulated market data", s.market_data, s.market_data === "STREAMING")}
      {line("Arbitrage engine", s.engine, s.engine === "RUNNING")}
      {line("Auto-trading (simulated)", s.paper_trading, s.paper_trading === "ENABLED", s.paper_trading === "DISABLED")}
      {s.halted_reason && <p className="mt-2 rounded bg-rose-500/10 p-2 text-xs text-rose-300">{s.halted_reason}</p>}
      <p className="mt-2 text-[11px] text-slate-500 num">uptime {Math.floor(s.uptime_s / 60)}m {s.uptime_s % 60}s · {s.quote_updates.toLocaleString()} quote updates</p>
    </section>
  );
}

function Kpis({ d }: { d: Snapshot }) {
  const p = d.portfolio, a = d.analytics;
  const kpi = (label: string, value: string, sub?: string, color = "text-white") => (
    <div className="card">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{label}</div>
      <div className={`num mt-1 text-xl sm:text-2xl font-bold ${color}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 gap-3 content-start">
      {kpi("Virtual equity", inr(p.equity, 0), `from ${inr(p.starting_capital, 0)}`)}
      {kpi("Realised P&L", signed(p.realized_pnl), `after ${inr(p.total_costs)} costs`, pnlColor(p.realized_pnl))}
      {kpi("Unrealised P&L", signed(p.unrealized_pnl), `${p.open_positions.length} open legged position(s)`, pnlColor(p.unrealized_pnl))}
      {kpi("Win rate", `${a.win_rate}%`, `avg ${signed(a.avg_net_pnl)} / trade`)}
      {kpi("Arbitrage trades", String(a.trades), `${a.completed} done · ${a.legged} legged · ${a.missed} missed`)}
      {kpi("Opportunities", d.detector.episodes.toLocaleString(), `${d.detector.profitable_episodes} profitable after costs`)}
      {kpi("Fill latency", `${a.latency_p50_ms} ms`, `p95 ${a.latency_p95_ms} ms`)}
      {kpi("Avg slippage", signed(a.avg_slippage), "+ worse / − better than detected", pnlColor(-a.avg_slippage))}
    </div>
  );
}

/* ------------------------------------------------------------------ prices */
function Px({ v, prev }: { v?: number; prev?: number }) {
  const cls = v !== undefined && prev !== undefined && v !== prev ? (v > prev ? "flash-up" : "flash-down") : "";
  return <span key={v} className={`num rounded px-1 ${cls}`}>{v !== undefined ? v.toFixed(2) : "–"}</span>;
}

function PriceTable({ rows }: { rows: QuoteRow[] }) {
  const prev = useRef<Record<string, QuoteRow>>({});
  const last = useMemo(() => prev.current, [rows]);
  useEffect(() => { prev.current = Object.fromEntries(rows.map((r) => [r.symbol, r])); }, [rows]);
  const cols = [["angelone", "NSE"], ["angelone", "BSE"], ["zerodha", "NSE"], ["zerodha", "BSE"]] as const;

  return (
    <section className="card overflow-x-auto">
      <h2 className="card-title">Broker prices · best bid / ask</h2>
      <table className="w-full min-w-[860px] text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500">
            <th className="py-2 pr-3">Instrument</th>
            {cols.map(([b, e]) => <th key={b + e} className="px-2" colSpan={2}>{BROKER_LABEL[b]} · {e}</th>)}
            <th className="px-2 text-right">Best NSE↔BSE gap</th>
          </tr>
          <tr className="text-left text-[10px] uppercase text-slate-600">
            <th />{cols.map(([b, e]) => [<th key={b + e + "b"} className="px-2">Bid</th>, <th key={b + e + "a"} className="px-2">Ask</th>])}<th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const nseBid = Math.max(r.angelone_NSE?.bid ?? -Infinity, r.zerodha_NSE?.bid ?? -Infinity);
            const nseAsk = Math.min(r.angelone_NSE?.ask ?? Infinity, r.zerodha_NSE?.ask ?? Infinity);
            const bseBid = Math.max(r.angelone_BSE?.bid ?? -Infinity, r.zerodha_BSE?.bid ?? -Infinity);
            const bseAsk = Math.min(r.angelone_BSE?.ask ?? Infinity, r.zerodha_BSE?.ask ?? Infinity);
            const gap = Math.max(bseBid - nseAsk, nseBid - bseAsk);
            const p = last[r.symbol];
            return (
              <tr key={r.symbol} className="border-t border-slate-800/70">
                <td className="py-2 pr-3 font-semibold text-white">{r.symbol}</td>
                {cols.map(([b, e]) => {
                  const k = `${b}_${e}` as const; const q = r[k]; const pq = p?.[k];
                  return [
                    <td key={k + "b"} className="px-1 text-emerald-300"><Px v={q?.bid} prev={pq?.bid} /></td>,
                    <td key={k + "a"} className="px-1 text-rose-300"><Px v={q?.ask} prev={pq?.ask} /></td>,
                  ];
                })}
                <td className={`px-2 text-right num font-semibold ${gap > 0 ? "text-amber-300" : "text-slate-500"}`}>
                  {Number.isFinite(gap) ? (gap > 0 ? "+" : "") + gap.toFixed(2) : "–"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

/* ------------------------------------------------------------------ opportunities */
function Opportunities({ items, stats }: { items: Opportunity[]; stats: Snapshot["detector"] }) {
  return (
    <section className="card">
      <div className="flex items-baseline justify-between">
        <h2 className="card-title">Arbitrage opportunities</h2>
        <span className="text-xs text-slate-500">{stats.active} open now</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 max-h-[460px] overflow-y-auto pr-1">
        {items.slice(0, 12).map((o) => (
          <div key={o.id} className={`rounded-lg border p-3 text-sm ${o.profitable ? "border-emerald-500/40 bg-emerald-500/5" : "border-slate-800 bg-slate-950/40"}`}>
            <div className="mb-2 flex items-center justify-between">
              <span className="font-bold text-white">{o.symbol}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${o.open ? "bg-cyan-500/15 text-cyan-300" : "bg-slate-700/40 text-slate-400"}`}>
                {o.open ? "OPEN" : `${(o.age_ms / 1000).toFixed(0)}s ago`}
              </span>
            </div>
            <Row l={<><b className="text-emerald-400">BUY</b> {o.buy.exchange} · {BROKER_LABEL[o.buy.broker]}</>} r={inr(o.buy.price)} />
            <Row l={<><b className="text-rose-400">SELL</b> {o.sell.exchange} · {BROKER_LABEL[o.sell.broker]}</>} r={inr(o.sell.price)} />
            <div className="my-1.5 border-t border-slate-800" />
            <Row l="Gross spread" r={`${inr(o.gross_spread)} × ${o.quantity}`} />
            <Row l="Estimated costs" r={inr(o.costs.total)} />
            <Row l={<b>Net P&amp;L</b>} r={<b className={pnlColor(o.net_pnl)}>{signed(o.net_pnl)}</b>} />
          </div>
        ))}
        {!items.length && <p className="text-sm text-slate-500">Scanning NSE and BSE quotes…</p>}
      </div>
    </section>
  );
}

function Row({ l, r }: { l: React.ReactNode; r: React.ReactNode }) {
  return <div className="flex justify-between gap-2 py-0.5 text-slate-300"><span>{l}</span><span className="num">{r}</span></div>;
}

/* ------------------------------------------------------------------ charts */
function PnlChart({ d }: { d: Snapshot }) {
  const series = d.analytics.pnl_series.map((p) => ({ t: p.t, pnl: p.pnl }));
  return (
    <section className="card">
      <h2 className="card-title">Cumulative realised P&amp;L (simulated)</h2>
      <div className="h-[420px]">
        {series.length < 2 ? <p className="text-sm text-slate-500">The chart appears after the first paper trades.</p> : (
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="t" tickFormatter={time} stroke="#64748b" fontSize={11} minTickGap={40} />
              <YAxis stroke="#64748b" fontSize={11} tickFormatter={(v) => "₹" + v} width={70} />
              <ReferenceLine y={0} stroke="#475569" />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} labelFormatter={(v) => time(Number(v))} formatter={(v: number) => [inr(v), "P&L"]} />
              <Line type="stepAfter" dataKey="pnl" stroke="#22d3ee" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

function SymbolChart({ d }: { d: Snapshot }) {
  const data = d.analytics.by_symbol;
  return (
    <section className="card">
      <h2 className="card-title">Net P&amp;L by instrument</h2>
      <div className="h-56">
        {!data.length ? <p className="text-sm text-slate-500">No trades yet.</p> : (
          <ResponsiveContainer>
            <BarChart data={data} layout="vertical" margin={{ left: 10, right: 10 }}>
              <XAxis type="number" stroke="#64748b" fontSize={11} tickFormatter={(v) => "₹" + v} />
              <YAxis type="category" dataKey="symbol" stroke="#94a3b8" fontSize={11} width={80} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} formatter={(v: number) => inr(v)} />
              <Bar dataKey="net_pnl" isAnimationActive={false}>
                {data.map((s) => <Cell key={s.symbol} fill={s.net_pnl >= 0 ? "#34d399" : "#fb7185"} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

function Rejections({ d }: { d: Snapshot }) {
  const entries = Object.entries(d.analytics.rejections);
  return (
    <section className="card">
      <h2 className="card-title">Risk engine · blocked signals</h2>
      {!entries.length ? <p className="text-sm text-slate-500">No signals blocked yet.</p> : (
        <ul className="space-y-1.5 text-sm">
          {entries.map(([k, v]) => <li key={k} className="flex justify-between"><span className="capitalize text-slate-300">{k}</span><span className="num text-amber-300">{v}</span></li>)}
        </ul>
      )}
      <p className="mt-4 text-xs text-slate-500">
        Signals are blocked when an order for the symbol is still in flight, during the cool-down after a trade,
        when per-minute, notional or daily-loss limits are hit, or when a simulated account lacks margin.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ trades */
function Trades({ trades }: { trades: Trade[] }) {
  const pill: Record<string, string> = {
    COMPLETED: "bg-emerald-500/15 text-emerald-300", LEGGED: "bg-amber-500/15 text-amber-300", MISSED: "bg-slate-600/30 text-slate-400",
  };
  const leg = (l: Trade["buy"]) => (
    <span className="num">
      {l.exchange}·{l.broker === "angelone" ? "AO" : "ZD"} {l.fill_price != null ? l.fill_price.toFixed(2) : <span className="text-slate-500">miss</span>}
    </span>
  );
  return (
    <section className="card overflow-x-auto">
      <h2 className="card-title">Arbitrage trade history</h2>
      <table className="w-full min-w-[900px] text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500">
            <th className="py-2">Time</th><th>Symbol</th><th>Status</th><th>Buy leg</th><th>Sell leg</th>
            <th className="text-right">Qty</th><th className="text-right">Expected</th><th className="text-right">Net P&amp;L</th>
            <th className="text-right">Slippage</th><th className="text-right">Latency</th><th className="pl-4">Note</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-t border-slate-800/70">
              <td className="py-1.5 num text-slate-400">{time(t.ts)}</td>
              <td className="font-semibold text-white">{t.symbol}</td>
              <td><span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${pill[t.status]}`}>{t.status}</span></td>
              <td className="text-emerald-300">{leg(t.buy)}</td>
              <td className="text-rose-300">{leg(t.sell)}</td>
              <td className="text-right num">{t.quantity}</td>
              <td className="text-right num text-slate-400">{signed(t.expected_net)}</td>
              <td className={`text-right num font-semibold ${pnlColor(t.net_pnl)}`}>{signed(t.net_pnl)}</td>
              <td className={`text-right num ${t.slippage > 0 ? "text-rose-300" : t.slippage < 0 ? "text-emerald-300" : "text-slate-400"}`}>{signed(t.slippage)}</td>
              <td className="text-right num text-slate-400">{t.latency_ms.toFixed(0)} ms</td>
              <td className="pl-4 text-xs text-slate-500 max-w-[260px] truncate" title={t.note}>{t.note}</td>
            </tr>
          ))}
          {!trades.length && <tr><td colSpan={11} className="py-6 text-center text-slate-500">No trades yet. Waiting for a spread that beats costs.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

/* ------------------------------------------------------------------ simulated accounts */
function Accounts({ accounts }: { accounts: Account[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {accounts.map((a) => {
        const usedPct = a.balance > 0 ? Math.min(100, (100 * a.used_margin) / a.balance) : 0;
        return (
          <section key={a.broker} className="card">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h2 className="text-base font-bold text-white">{a.name}</h2>
                <p className="num text-xs text-slate-500">Client ID {a.client_id} · Intraday (MIS) · {a.orders} orders</p>
              </div>
              <span className="rounded bg-violet-500/15 px-2 py-0.5 text-[10px] font-bold text-violet-300">{a.account_type} ACCOUNT</span>
            </div>
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
              <Stat label="Opening balance" value={inr(a.opening_balance, 0)} />
              <Stat label="Account balance" value={inr(a.balance)} />
              <Stat label="Available margin" value={inr(a.available_margin)} />
              <Stat label="Realised P&L" value={signed(a.realized_pnl)} color={pnlColor(a.realized_pnl)} />
              <Stat label="Charges paid" value={inr(a.charges)} />
              <Stat label="Used margin" value={inr(a.used_margin)} />
            </div>
            <div className="mt-3 h-1.5 rounded-full bg-slate-800">
              <div className="h-1.5 rounded-full bg-cyan-400 transition-all" style={{ width: `${usedPct}%` }} />
            </div>
          </section>
        );
      })}
    </div>
  );
}

function Stat({ label, value, color = "text-white" }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`num font-semibold ${color}`}>{value}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ simulated orders */
function Orders({ orders, stats }: { orders: Order[]; stats: Record<string, number> }) {
  const pill: Record<string, string> = {
    COMPLETE: "bg-emerald-500/15 text-emerald-300", CANCELLED: "bg-slate-600/30 text-slate-300",
    REJECTED: "bg-rose-500/15 text-rose-300", OPEN: "bg-cyan-500/15 text-cyan-300",
  };
  return (
    <section className="card overflow-x-auto">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="card-title">Simulated order book</h2>
        <span className="text-xs text-slate-500 num">
          {Object.entries(stats).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") || "no orders yet"}
        </span>
      </div>
      <table className="w-full min-w-[980px] text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500">
            <th className="py-2">Time</th><th>Order ID</th><th>Account</th><th>Symbol</th><th>Side</th><th>Type</th>
            <th className="text-right">Qty</th><th className="text-right">Price</th><th className="text-right">Avg fill</th>
            <th className="pl-4">Status</th><th className="text-right">Latency</th><th className="pl-4">Reason</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.order_id} className="border-t border-slate-800/70">
              <td className="py-1.5 num text-slate-400">{time(o.placed_ts)}</td>
              <td className="num text-xs text-slate-300">{o.order_id}</td>
              <td className="text-slate-300">{BROKER_LABEL[o.broker]} · {o.exchange}</td>
              <td className="font-semibold text-white">{o.symbol}</td>
              <td className={o.side === "BUY" ? "font-bold text-emerald-400" : "font-bold text-rose-400"}>{o.side}</td>
              <td className="text-xs text-slate-400">{o.order_type} · {o.validity}</td>
              <td className="text-right num">{o.quantity}</td>
              <td className="text-right num text-slate-400">{o.price != null ? o.price.toFixed(2) : "MKT"}</td>
              <td className="text-right num">{o.fill_price != null ? o.fill_price.toFixed(2) : "–"}</td>
              <td className="pl-4"><span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${pill[o.status]}`}>{o.status}</span></td>
              <td className="text-right num text-slate-400">{o.latency_ms.toFixed(0)} ms</td>
              <td className="pl-4 text-xs text-slate-500 max-w-[240px] truncate" title={o.reason}>{o.reason}</td>
            </tr>
          ))}
          {!orders.length && <tr><td colSpan={12} className="py-6 text-center text-slate-500">No simulated orders yet.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

/* ------------------------------------------------------------------ risk */
function RiskPanel({ risk }: { risk: Risk }) {
  const [form, setForm] = useState<Risk>(risk);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { if (!dirty) setForm(risk); }, [risk, dirty]);

  const fields: [keyof Risk, string][] = [
    ["min_net_pnl", "Min net P&L / trade (₹)"], ["max_quantity", "Max quantity"], ["max_notional", "Max notional / leg (₹)"],
    ["max_trades_per_minute", "Max trades / minute"], ["daily_loss_limit", "Daily loss limit (₹)"], ["symbol_cooldown_ms", "Symbol cool-down (ms)"],
  ];
  const save = async () => {
    try { await api("/api/risk", "PUT", form); setMsg("Saved"); setDirty(false); }
    catch (e) { setMsg("Invalid value"); }
    setTimeout(() => setMsg(""), 2000);
  };
  return (
    <section className="card">
      <h2 className="card-title">Risk limits</h2>
      <div className="space-y-2">
        {fields.map(([k, label]) => (
          <label key={k} className="flex items-center justify-between gap-3 text-sm">
            <span className="text-slate-300">{label}</span>
            <input type="number" value={form[k]} min={0}
              onChange={(e) => { setDirty(true); setForm({ ...form, [k]: Number(e.target.value) }); }}
              className="w-28 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-right num text-white focus:border-cyan-500 outline-none" />
          </label>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button onClick={save} disabled={!dirty} className="rounded-lg bg-cyan-500 px-4 py-1.5 text-sm font-bold text-slate-950 disabled:opacity-40">Apply</button>
        <span className="text-xs text-emerald-300">{msg}</span>
      </div>
    </section>
  );
}
