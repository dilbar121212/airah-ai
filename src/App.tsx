import { useState } from 'react';
import { Mic, Send, Plus, Globe2, Sparkles, MessageSquare, BookOpen, FileText, Settings, Volume2 } from 'lucide-react';

type Message = { role: 'user' | 'assistant'; text: string };
const languages = [{id:'auto',name:'Auto detect'},{id:'ur',name:'اردو'},{id:'en',name:'English'},{id:'ar',name:'العربية'},{id:'hi',name:'हिन्दी'}];

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [language, setLanguage] = useState('auto');
  const [busy, setBusy] = useState(false);
  const rtl = language === 'ur' || language === 'ar';

  async function send(text = input) {
    const value = text.trim();
    if (!value || busy) return;
    setMessages(m => [...m, {role:'user', text:value}]);
    setInput(''); setBusy(true);
    try {
      // Same-origin call: Vite proxies /api to the FastAPI backend, so this
      // works in Replit, locally, and anywhere else without a localhost URL.
      // VITE_API_URL can override it (e.g. a deployed backend URL).
      const apiUrl = `${import.meta.env.VITE_API_URL ?? ''}/api/chat`;
      const res = await fetch(apiUrl, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({message:value, language})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Request failed');
      setMessages(m => [...m, {role:'assistant', text:data.answer}]);
    } catch (e) {
      setMessages(m => [...m, {role:'assistant', text:`Connection issue: ${e instanceof Error ? e.message : 'Please check the backend.'}`}]);
    } finally { setBusy(false); }
  }

  function speak(text: string) {
    if (!('speechSynthesis' in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = language === 'ur' ? 'ur-PK' : language === 'ar' ? 'ar-SA' : language === 'hi' ? 'hi-IN' : 'en-US';
    window.speechSynthesis.speak(utterance);
  }

  function listen() {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) { alert('Voice input is not supported in this browser. You can type instead.'); return; }
    const recognition = new SpeechRecognition();
    recognition.lang = language === 'ur' ? 'ur-PK' : language === 'ar' ? 'ar-SA' : language === 'hi' ? 'hi-IN' : 'en-US';
    recognition.onresult = (event: any) => setInput(event.results[0][0].transcript);
    recognition.start();
  }

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={22}/></div><div><b>Airah AI</b><small>PERSONAL ASSISTANT</small></div></div>
      <button className="new-chat" onClick={() => setMessages([])}><Plus size={17}/> New conversation</button>
      <div className="side-label">WORKSPACE</div>
      <div className="nav active"><MessageSquare size={17}/> AI Chat</div>
      <div className="nav"><BookOpen size={17}/> Study Assistant</div>
      <div className="nav"><FileText size={17}/> Documents</div>
      <div className="nav"><Globe2 size={17}/> Web research <span className="soon">NEXT</span></div>
      <div className="side-bottom"><Settings size={17}/> Settings <span className="status-dot"/> Online UI</div>
    </aside>
    <main className="main">
      <header><div><span className="eyebrow">YOUR MULTILINGUAL AI</span><h1>How can I help today?</h1></div>
        <label className="language"><Globe2 size={16}/><select value={language} onChange={e=>setLanguage(e.target.value)}>{languages.map(l=><option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
      </header>
      <section className="content">
        {messages.length === 0 ? <div className="welcome">
          <div className="orb"><div className="orb-core"><Sparkles size={34}/></div></div>
          <h2>Your ideas, amplified.</h2><p>Ask a question, explore an idea, or tell Airah what you need.</p>
          <div className="suggestions">
            {['Explain a difficult topic simply','Help me write something','Summarize a document','Practice a new language'].map((s,i)=><button key={i} onClick={()=>send(s)}><Sparkles size={15}/>{s}</button>)}
          </div>
        </div> : <div className="messages" dir={rtl?'rtl':'auto'}>{messages.map((m,i)=><div className={`message ${m.role}`} key={i}><div className="avatar">{m.role==='assistant'?'A':'You'}</div><div className="bubble">{m.text}{m.role==='assistant'&&<button className="speak" onClick={()=>speak(m.text)} title="Read aloud"><Volume2 size={15}/></button>}</div></div>)}{busy&&<div className="typing">Airah is thinking…</div>}</div>}
      </section>
      <footer><div className="composer"><textarea value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}} placeholder="Message Airah..." rows={1}/><button className="icon-btn" onClick={listen} title="Voice input"><Mic size={19}/></button><button className="send" onClick={()=>send()} disabled={!input.trim()||busy}><Send size={18}/></button></div><small>Airah can make mistakes. Verify important information.</small></footer>
    </main>
  </div>;
}
