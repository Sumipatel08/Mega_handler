import { useEffect, useState } from "react";
import "@/App.css";
import axios from "axios";
import { Bot, HardDrive, RefreshCw, Shuffle, Terminal } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const COMMANDS = [
  ["/newaccount email password", "Aapke email se naya MEGA account, phir email ka confirm link bot ko bhejo"],
  ["/addaccount email password", "Pehle se bana MEGA account jodo (password wala message bot khud delete karega)"],
  ["/accounts", "Saare accounts, khali jagah, switch ya hatao"],
  ["/folders", "Buttons se folder kholo, rename karo, save folder chuno"],
  ["/newfolder naam", "Selected folder mein naya folder"],
  ["/auto", "Link sabse zyada khali jagah wale account mein jaye"],
  ["/search naam", "Saare accounts mein file ya folder dhoondo"],
  ["/status", "Abhi ka account, save folder aur auto switch"],
  ["/wallpaper", "Random anime wallpaper"],
  ["MEGA link bhejo", "Cloud mein save, aur badle mein aapke account ka share link"],
];

const HERO = "https://static.prod-images.emergentagent.com/jobs/73ca01de-7b97-454b-a19b-b49039c92a31/images/e1c8bfcdbe14f935f045b9e8f082e2b173abcc488c0883a2c84ab7c55fc4504d.jpeg";

const gb = (n) => `${(n / 1073741824).toFixed(2)} GB`;

const StatusCard = ({ status }) => {
  const ok = status?.running;
  return (
    <div data-testid="bot-status-card" className="panel">
      <div className="flex items-center gap-3">
        <span className={`dot ${ok ? "dot-on" : "dot-off"}`} />
        <span data-testid="bot-status-text" className="text-sm uppercase tracking-[0.2em]">
          {ok ? "Bot chal raha hai" : "Bot band hai"}
        </span>
      </div>
      {ok ? (
        <a data-testid="bot-telegram-link" href={`https://t.me/${status.username}`} target="_blank" rel="noreferrer" className="handle">
          @{status.username}
        </a>
      ) : (
        <p data-testid="bot-status-error" className="mt-4 text-sm text-[var(--muted)]">
          {status?.error || "Load ho raha hai..."}
        </p>
      )}
      <div className="mt-6 flex flex-wrap gap-3 text-xs">
        <span data-testid="owner-lock-badge" className="chip">{status?.owner_set ? "Sirf aap (owner lock ON)" : "Owner ID set nahi"}</span>
        <span data-testid="auto-switch-badge" className="chip"><Shuffle size={12} /> Auto switch {status?.auto_switch ? "ON" : "OFF"}</span>
      </div>
    </div>
  );
};

const AccountRow = ({ a, active }) => {
  const pct = a.total ? Math.min(100, (a.used / a.total) * 100) : 0;
  return (
    <li data-testid={`account-row-${a.id}`} className="acc">
      <div className="flex justify-between gap-4">
        <div>
          <p className="font-semibold">{a.label} {active && <span className="chip ml-2">active</span>}</p>
          <p className="text-xs text-[var(--muted)]">{a.email} · {a.save_folder_name || "Cloud Drive"}</p>
        </div>
        <p data-testid={`account-free-${a.id}`} className="text-sm whitespace-nowrap">{gb(a.total - a.used)} khali</p>
      </div>
      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
    </li>
  );
};

export default function App() {
  const [status, setStatus] = useState(null);
  const [accounts, setAccounts] = useState([]);

  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try {
      const [s, a] = await Promise.all([axios.get(`${API}/bot/status`), axios.get(`${API}/accounts`)]);
      setStatus(s.data);
      setAccounts(a.data);
    } catch (e) {
      setStatus({ running: false, error: "Server se connect nahi ho paya. Refresh karo." });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <main className="shell">
      <header className="rise hero" style={{ backgroundImage: `linear-gradient(90deg, rgba(13,15,14,0.95) 35%, rgba(13,15,14,0.35)), url(${HERO})` }}>
        <p className="eyebrow"><Bot size={14} /> Telegram x MEGA</p>
        <h1 className="text-4xl sm:text-5xl lg:text-6xl">MEGA Manager Bot</h1>
        <p className="mt-4 max-w-xl text-base text-[var(--muted)]">
          Kai MEGA accounts, ek bot. Folder banao, links cloud mein save karo, naam badlo, sab Telegram se.
        </p>
      </header>

      <section className="grid gap-6 lg:grid-cols-[1fr_1.4fr] mt-14">
        <div className="space-y-6 rise" style={{ animationDelay: "120ms" }}>
          <StatusCard status={status} />
          <div className="panel">
            <p className="eyebrow"><Terminal size={14} /> Commands</p>
            <ul className="space-y-4 mt-4">
              {COMMANDS.map(([c, d]) => (
                <li key={c}><code className="cmd">{c}</code><p className="text-sm text-[var(--muted)] mt-1">{d}</p></li>
              ))}
            </ul>
          </div>
        </div>

        <div className="panel rise" style={{ animationDelay: "220ms" }}>
          <div className="flex items-center justify-between">
            <p className="eyebrow"><HardDrive size={14} /> Jude hue accounts ({accounts.length})</p>
            <button data-testid="refresh-accounts-button" onClick={load} disabled={busy} className="iconbtn" aria-label="Refresh"><RefreshCw size={16} className={busy ? "animate-spin" : ""} /></button>
          </div>
          {accounts.length === 0 ? (
            <p data-testid="no-accounts-message" className="mt-6 text-sm text-[var(--muted)]">
              Abhi koi account nahi. Bot ko /addaccount email password bhejo.
            </p>
          ) : (
            <ul data-testid="accounts-list" className="mt-6 space-y-4">
              {accounts.map((a) => <AccountRow key={a.id} a={a} active={a.id === status?.active_account_id} />)}
            </ul>
          )}
        </div>
      </section>
      <footer data-testid="credit-footer" className="credit">Made by <span>DD FREE DISH</span></footer>
    </main>
  );
}
