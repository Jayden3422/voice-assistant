// TTS_MODE:
// - "backend" (default): use Backend /tts
// - "browser": use Web Speech API (speechSynthesis)
// - "auto": try backend first, fallback to browser if enabled
export const TTS_MODE = import.meta.env.VITE_TTS_MODE || "backend";

// Enable browser TTS fallback when TTS_MODE is "auto".
// Set VITE_TTS_BROWSER=true to allow fallback.
export const ENABLE_BROWSER_TTS = import.meta.env.VITE_TTS_BROWSER === "true";
