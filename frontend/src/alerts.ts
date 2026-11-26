import { useEffect, useRef } from "react";
import { Alert } from "./api";

/** Soft alert chime (Web Audio): sine tones with a gentle fade, kept quiet on purpose.
 * Caution: one soft note every 4 s. Warning: a two-note chime every 1.5 s. Silent when clear. */
export function useAlertSound(alert: Alert, enabled: boolean, volume: number) {
  const ctxRef = useRef<AudioContext | null>(null);
  const lastRef = useRef(0);

  // browsers only allow audio after a user gesture: unlock on the first click/key
  useEffect(() => {
    const unlock = () => {
      if (!ctxRef.current) ctxRef.current = new AudioContext();
      void ctxRef.current.resume();
    };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => { window.removeEventListener("pointerdown", unlock); window.removeEventListener("keydown", unlock); };
  }, []);

  useEffect(() => {
    if (!enabled || alert.S === 0) return;
    const play = () => {
      const ctx = ctxRef.current;
      if (!ctx || ctx.state !== "running") return;
      const peak = 0.06 * Math.max(0, Math.min(1, volume));          // never loud
      const notes = alert.S >= 2 ? [740, 988] : [587];
      notes.forEach((f, i) => {
        const t = ctx.currentTime + i * 0.16;
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(peak, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.45);
        o.connect(g).connect(ctx.destination);
        o.start(t); o.stop(t + 0.5);
      });
      lastRef.current = performance.now();
    };
    const every = alert.S >= 2 ? 1500 : 4000;
    if (performance.now() - lastRef.current > every * 0.6) play();   // react promptly when the level rises
    const id = window.setInterval(play, every);
    return () => clearInterval(id);
  }, [alert.S, enabled, volume]);
}
