import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Sparkles, Plus, Send, Mic, Volume2, Copy, RefreshCw, Square, Trash2, Menu, Globe } from "lucide-react";
import { detectLang, voiceFor, stopAudio, setCurrent } from "./lang";

const LANGS = [{ id: "auto", name: "Auto detect" }, { id: "ur", name: "اردو" },
  { id: "en", name: "English" }, { id: "ar", name: "العربية" }, { id: "hi", name: "हिन्दी" }];
const KEY = "airah.chats.v1";
const uid = () => Math.random().toString(36).slice(2, 10);
type Msg = { role: "user" | "assistant"; text: string; error?: boolean };
type Chat = { id: string; title: string; messages: Msg[] };
const load = (): Chat[] => { try { return JSON.parse(localStorage.getItem(KEY) || "[]") || []; } catch { return []; } };
const save = (c: Chat[]) => { try { localStorage.setItem(KEY, JSON.stringify(c.slice(0, 50))); } catch { /* ignore */ } };
const ERR = "Sorry, I could not answer right now. Please try again.";
const RATE = "Too many requests. Please wait a minute and try again.";

export default function AirahChat() {
  const [chats, setChats] = useState<Chat[]>(load);
  const [cid, setCid] = useState<string | null>(() => load()[0]?.id || null);
  const [text, setText] = useState("");
  const [lang, setLang] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const ctl = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement | null>(null);
  const msgs = chats.find((c) => c.id === cid)?.messages || [];

  useEffect(() => save(chats), [chats]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs.length, msgs[msgs.length - 1]?.text]);
  useEffect(() => { if (note) { const t = setTimeout(() => setNote(""), 3500); return () => clearTimeout(t); } }, [note]);
  useEffect(() => { document.documentElement.lang = lang === "auto" ? "en" : lang; }, [lang]);

  const patch = (id: string, fn: (m: Msg[]) => Msg[]) => setChats((cs) => cs.map((c) => (c.id === id ? { ...c, messages: fn(c.messages) } : c)));

  async function ask(id: string, history: { role: "user" | "assistant"; content: string }[]) {
    setBusy(true);
    ctl.current = new AbortController();
    patch(id, (m) => [...m, { role: "assistant", text: "" }]);
    const set = (t: string, error?: boolean) => patch(id, (m) => m.map((x, i) => (i === m.length - 1 ? { ...x, text: t, error } : x)));
    const body = JSON.stringify({
      message: history[history.length - 1].content, language: lang,
      history: history.slice(0, -1).slice(-20),
    });
    const opt: RequestInit = { method: "POST", headers: { "Content-Type": "application/json" }, body, signal: ctl.current.signal };
    let acc = "";
    try {
      const r = await fetch("/api/chat/stream", opt);
      if (r.status === 429) { set(RATE, true); return; }
      if (r.ok && r.body) {
        const rd = r.body.getReader(), dec = new TextDecoder();
        for (;;) {
          const { done, value } = await rd.read();
          if (done) break;
          acc += dec.decode(value, { stream: true });
          set(acc);
        }
        if (!acc) throw new Error("empty");
      } else {
        const r2 = await fetch("/api/chat", opt);
        if (r2.status === 429) { set(RATE, true); return; }
        const j = await r2.json();
        if (!r2.ok) throw new Error("failed");
        set(j.answer);
      }
    } catch (e) {
      const name = e instanceof Error ? e.name : "";
      if (name !== "AbortError") set(acc || ERR, !acc);   // raw backend errors are never shown
    } finally { setBusy(false); }
  }

  function send(raw = text) {
    const q = raw.trim();
    if (!q || busy) return;
    let id = cid;
    if (!id) { id = uid(); setChats((cs) => [{ id: id as string, title: q.slice(0, 40), messages: [] }, ...cs]); setCid(id); }
    const hist: Msg[] = [...(chats.find((c) => c.id === id)?.messages || []), { role: "user", text: q }];
    patch(id, () => hist);
    setText("");
    ask(id, hist.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.text })));
  }

  function regenerate() {
    if (busy || !cid) return;
    const hist = msgs.slice(0, -1);
    patch(cid, () => hist);
    ask(cid, hist.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.text })));
  }

  async function speak(t: string) {
    const l = detectLang(t);
    stopAudio();
    try {
      const r = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: t.slice(0, 1500), language: l, voice: voiceFor(l) }) });
      if (r.ok) {
        const url = URL.createObjectURL(await r.blob());
        const audio = new Audio(url); audio.onended = () => URL.revokeObjectURL(url);
        setCurrent(audio);
        await audio.play(); return;
      }
    } catch { /* fall through to browser TTS */ }
    if ("speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance(t);
      u.lang = { ur: "ur-PK", "roman-ur": "ur-PK", hi: "hi-IN", ar: "ar-SA" }[l] || "en-US";
      const v = speechSynthesis.getVoices().find((x) => x.lang?.toLowerCase().startsWith(u.lang.slice(0, 2).toLowerCase()));
      if (!v && u.lang !== "en-US") { setNote("No voice for this language on your device."); return; }
      if (v) u.voice = v;
      speechSynthesis.speak(u);
    } else setNote("Read aloud is not supported on this device.");
  }

  function mic() {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) { setNote("Voice input works in Chrome or Edge. You can type instead."); return; }
    const r = new SR();
    r.lang = { ur: "ur-PK", ar: "ar-SA", hi: "hi-IN" }[lang] || "en-US";
    r.onstart = () => setListening(true);
    r.onend = r.onerror = () => setListening(false);
    r.onresult = (e: any) => setText(e.results[0][0].transcript);
    r.start();
  }

  const rtl = lang === "ur" || lang === "ar";
  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand"><div className="brand-mark"><Sparkles size={22} /></div>
          <div><b>Airah AI</b><small>PERSONAL ASSISTANT</small></div></div>
        <button className="new-chat" onClick={() => { setCid(null); setOpen(false); }}><Plus size={17} /> New conversation</button>
        <div className="side-label">RECENT</div>
        <nav className="ax-list" aria-label="Conversations">
          {chats.map((c) => (
            <div key={c.id} className={`ax-item ${c.id === cid ? "active" : ""}`}>
              <button onClick={() => { setCid(c.id); setOpen(false); }}>{c.title}</button>
              <button aria-label={`Delete ${c.title}`} onClick={() => { setChats((cs) => cs.filter((x) => x.id !== c.id)); if (c.id === cid) setCid(null); }}><Trash2 size={14} /></button>
            </div>
          ))}
          {!chats.length && <p className="ax-empty">No conversations yet.</p>}
        </nav>
      </aside>

      <main className="main">
        <header>
          <button className="ax-menu icon-btn" aria-label="Open menu" onClick={() => setOpen((o) => !o)}><Menu size={20} /></button>
          <div><span className="eyebrow">YOUR MULTILINGUAL AI</span><h1>How can I help today?</h1></div>
          <label className="language"><Globe size={16} /><span className="ax-sr">Language</span>
            <select value={lang} onChange={(e) => setLang(e.target.value)}>{LANGS.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        </header>

        <section className="content">
          {!msgs.length ? (
            <div className="welcome">
              <div className="orb"><div className="orb-core"><Sparkles size={34} /></div></div>
              <h2>Your ideas, amplified.</h2>
              <p>Ask a question, explore an idea, or tell Airah what you need.</p>
              <div className="suggestions">
                {["Explain a difficult topic simply", "Help me write something", "Translate this into Urdu", "Practice a new language"].map((s) => (
                  <button key={s} onClick={() => send(s)}><Sparkles size={15} />{s}</button>))}
              </div>
            </div>
          ) : (
            <div className="messages" dir={rtl ? "rtl" : "auto"} aria-live="polite">
              {msgs.map((m, i) => (
                <div key={i} className={`message ${m.role}`}>
                  <div className="avatar" aria-hidden="true">{m.role === "assistant" ? "A" : "You"}</div>
                  <div className="bubble" dir="auto">
                    {m.role === "assistant"
                      ? (m.text ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown> : <span className="ax-dots" aria-label="Airah is typing">●●●</span>)
                      : m.text}
                    {m.role === "assistant" && m.text && (
                      <div className="ax-actions">
                        <button aria-label="Read aloud" onClick={() => speak(m.text)}><Volume2 size={15} /></button>
                        <button aria-label="Copy" onClick={() => { navigator.clipboard?.writeText(m.text); setNote("Copied"); }}><Copy size={15} /></button>
                        {m.error && <button onClick={regenerate}>Try again</button>}
                        {i === msgs.length - 1 && !m.error && !busy && <button aria-label="Regenerate" onClick={regenerate}><RefreshCw size={15} /></button>}
                      </div>)}
                  </div>
                </div>))}
              <div ref={end} />
            </div>
          )}
        </section>

        <footer>
          {note && <div className="ax-toast" role="status">{note}</div>}
          <div className="composer">
            <textarea aria-label="Message Airah" value={text} rows={1} placeholder="Message Airah..."
              onChange={(e) => setText(e.target.value)} maxLength={4000}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
            <button className={`icon-btn ${listening ? "ax-rec" : ""}`} aria-label={listening ? "Listening…" : "Voice input"} onClick={mic}><Mic size={19} /></button>
            {busy
              ? <button className="send" aria-label="Stop" onClick={() => ctl.current?.abort()}><Square size={16} /></button>
              : <button className="send" aria-label="Send" disabled={!text.trim()} onClick={() => send()}><Send size={18} /></button>}
          </div>
          <small>Airah can make mistakes. Verify important information.</small>
        </footer>
      </main>
    </div>
  );
}
