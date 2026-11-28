// Helpers kept from the original App.tsx, renamed per the upgrade plan.
// Their bodies are unchanged: the language detector, the voice-name
// picker, and the audio stopper. The shared audio handle is `current`
// (was `webTtsAudio`); stopAudio() pauses it and cancels browser speech.

const URDU_WORDS = ['hai','hain','kya','karo','mujhe','mera','meri','aap','tum','mein','nahi','nahin','kaise','kaisa','batao','suno','dekho','acha','theek','shukriya','salam','assalam','ka','ki','ke','se','aur','lekin','kab','kahan','kyun','chahiye','bolo','likho','parho','samjho','waqt','din','raat','subah','shaam','ghar','kaam','baat','dil','dost','bhai','ammi','abba','beta','naam','sawal','jawab','kuch','koi','kabhi','hamesha','roz','abhi','aaj','kal','phir','bhi','bohat','bahut','bilkul','zaroor'];

export function detectLang(text: string): string {
  if (/[\u0600-\u06FF]/.test(text)) return 'ur';
  if (/[\u0900-\u097F]/.test(text)) return 'hi';
  const words = text.toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/).filter(Boolean);
  if (!words.length) return 'en';
  const hits = words.filter(w => URDU_WORDS.includes(w)).length;
  return hits / words.length >= 0.25 ? 'roman-ur' : 'en';
}

export function voiceFor(lang: string): string {
  if (lang === 'en') return 'en-US-AriaNeural';
  if (lang === 'hi') return 'hi-IN-SwaraNeural';
  if (lang === 'ar') return 'ar-SA-ZariyahNeural';
  return 'ur-PK-UzmaNeural';
}

export let current: HTMLAudioElement | null = null;

export function setCurrent(audio: HTMLAudioElement | null) {
  current = audio;
}

export function stopAudio() {
  if (current) { try { current.pause(); } catch { /* ignore */ } current = null; }
  if ('speechSynthesis' in window) { try { window.speechSynthesis.cancel(); } catch { /* ignore */ } }
}
