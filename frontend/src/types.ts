export type BrokerId = "angelone" | "zerodha";
export type ExchangeId = "NSE" | "BSE";

export interface Side { bid: number; ask: number; bid_qty: number; ask_qty: number; age_ms: number; latency_ms: number }
export type QuoteRow = { symbol: string } & Record<`${BrokerId}_${ExchangeId}`, Side | null>;

export interface FeedStatus {
  broker: BrokerId; name: string; state: string; messages: number; reconnects: number;
  last_message_age_ms: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null; last_error: string | null;
}

export interface Status {
  mode: string; feeds: FeedStatus[]; market_data: string; engine: string;
  paper_trading: string; halted_reason: string | null; uptime_s: number; quote_updates: number;
}

export interface Costs { brokerage: number; stt: number; exchange_txn: number; sebi: number; stamp_duty: number; gst: number; total: number }

export interface Opportunity {
  id: string; symbol: string; quantity: number;
  buy: { exchange: ExchangeId; broker: BrokerId; price: number };
  sell: { exchange: ExchangeId; broker: BrokerId; price: number };
  gross_spread: number; net_spread: number; gross_pnl: number; costs: Costs; net_pnl: number;
  profitable: boolean; detected_ts: number; open: boolean; age_ms: number;
}

export interface Leg {
  side: "BUY" | "SELL"; exchange: ExchangeId; broker: BrokerId; limit: number; quantity: number; fill_price: number | null;
  status: string; latency_ms: number; order_id: string | null; reason: string;
}

export interface Account {
  broker: BrokerId; name: string; client_id: string; account_type: string; opening_balance: number; realized_pnl: number;
  charges: number; balance: number; used_margin: number; available_margin: number; orders: number;
}

export interface Order {
  order_id: string; broker: BrokerId; exchange: ExchangeId; symbol: string; side: "BUY" | "SELL"; quantity: number;
  order_type: "LIMIT" | "MARKET"; price: number | null; validity: string; tag: string;
  status: "OPEN" | "COMPLETE" | "CANCELLED" | "REJECTED"; filled_qty: number; fill_price: number | null;
  reason: string; latency_ms: number; placed_ts: number; updated_ts: number;
}

export interface Trade {
  id: string; opportunity_id: string; symbol: string; quantity: number; status: "COMPLETED" | "LEGGED" | "MISSED";
  expected_net: number; gross_pnl: number; costs: number; net_pnl: number; slippage: number; latency_ms: number;
  note: string; ts: number; buy: Leg; sell: Leg;
}

export interface Portfolio {
  starting_capital: number; realized_pnl: number; unrealized_pnl: number; equity: number; total_costs: number; accounts: Account[];
  open_positions: { trade_id: string; symbol: string; side: string; exchange: ExchangeId; broker: BrokerId; quantity: number; entry: number; mark: number }[];
}

export interface Analytics {
  trades: number; completed: number; legged: number; missed: number; win_rate: number; avg_net_pnl: number;
  avg_slippage: number; latency_p50_ms: number; latency_p95_ms: number;
  by_symbol: { symbol: string; trades: number; net_pnl: number }[];
  rejections: Record<string, number>; pnl_series: { t: number; pnl: number }[];
}

export interface Risk {
  min_net_pnl: number; max_quantity: number; max_notional: number; max_trades_per_minute: number;
  daily_loss_limit: number; max_quote_age_ms: number; symbol_cooldown_ms: number;
}

export interface Snapshot {
  type: "snapshot"; ts: number; status: Status; quotes: QuoteRow[]; opportunities: Opportunity[];
  detector: { episodes: number; profitable_episodes: number; active: number; evaluations: number };
  trades: Trade[]; orders: Order[]; order_stats: Record<string, number>; portfolio: Portfolio; analytics: Analytics; risk: Risk;
}

export const BROKER_LABEL: Record<BrokerId, string> = { angelone: "Angel One (Sim)", zerodha: "Zerodha (Sim)" };
