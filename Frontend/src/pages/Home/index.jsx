import { useState, useRef, useEffect } from "react";
import { Button, message as AntMessage } from "antd";
import "./index.scss";
import * as api from "../../utils/api";
import { useI18n } from "../../i18n/LanguageContext.jsx";
import { ENABLE_BROWSER_TTS, TTS_MODE } from "../../config/tts.js";

const Home = () => {
  const { t, lang } = useI18n();
  const [hasStarted, setHasStarted] = useState(false);
  const [messages, setMessages] = useState([]);
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const mediaRecorderRef = useRef(null);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleStartConversation = () => {
    setHasStarted(true);
    const greeting = {
      id: Date.now(),
      role: "ai",
      text: t("home.greeting"),
    };
    setMessages([greeting]);
    playGreetingSpeech(t("home.greeting"));
  };

  const speakWithBrowserTTS = (text) => {
    if (!("speechSynthesis" in window)) return false;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = lang === "zh" ? "zh-CN" : "en-US";
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    return true;
  };

  const requestBackendTTS = async (text) => {
    const res = await api.postAPI("/tts", { text, lang });
    const data = res && res.data ? res.data : res || {};
    if (data.audio_base64) {
      playBase64Audio(data.audio_base64);
      return true;
    }
    return false;
  };

  const playGreetingSpeech = async (text) => {
    if (TTS_MODE === "browser") {
      speakWithBrowserTTS(text);
      return;
    }
    if (TTS_MODE === "backend") {
      try {
        await requestBackendTTS(text);
      } catch (err) {
        console.error("Backend TTS failed:", err);
      }
      return;
    }
    if (TTS_MODE === "auto") {
      try {
        const ok = await requestBackendTTS(text);
        if (ok) return;
      } catch (err) {
        console.error("Backend TTS failed:", err);
      }
      if (ENABLE_BROWSER_TTS) {
        speakWithBrowserTTS(text);
      }
    }
  };

  const playBase64Audio = (base64, mimeType = "audio/wav") => {
    try {
      const byteString = atob(base64);
      const ab = new ArrayBuffer(byteString.length);
      const ia = new Uint8Array(ab);
      for (let i = 0; i < byteString.length; i += 1) {
        ia[i] = byteString.charCodeAt(i);
      }
      const blob = new Blob([ab], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.play();
    } catch (err) {
      console.error(`${t("errors.playAiAudio")}:`, err);
    }
  };

  const sendAudioToBackend = async (blob) => {
    const formData = new FormData();
    formData.append("audio", blob, "voice.webm");
    formData.append("lang", lang);

    setIsProcessing(true);
    await api
      .postAPI("/voice", formData)
      .then((res) => {
        const data = res && res.data ? res.data : res || {};
        const { user_text, ai_text, audio_base64 } = data;
        const newMessages = [];
        if (user_text) {
          newMessages.push({
            id: Date.now(),
            role: "user",
            text: user_text,
          });
        }
        if (ai_text) {
          newMessages.push({
            id: Date.now() + 1,
            role: "ai",
            text: ai_text,
          });
        }
        if (newMessages.length > 0) {
          setMessages((prev) => [...prev, ...newMessages]);
        }
        if (audio_base64) {
          playBase64Audio(audio_base64);
        }
      })
      .catch((err) => {
        console.error(`${t("errors.processingFailed")}:`, err);
        AntMessage.error(t("errors.processingFailed"));
      })
      .finally(() => {
        setIsProcessing(false);
      });
  };

  const handleStartRecording = async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      AntMessage.error(t("errors.browserNotSupported"));
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      const chunks = [];
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      };
      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        stream.getTracks().forEach((track) => track.stop());
        sendAudioToBackend(blob);
      };
      mediaRecorder.start();
      mediaRecorderRef.current = mediaRecorder;
      setIsRecording(true);
    } catch (err) {
      console.error(`${t("errors.micDenied")}:`, err);
      AntMessage.error(t("errors.micDenied"));
    }
  };

  const handleStopRecording = () => {
    if (mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
      setIsRecording(false);
    }
  };

  if (!hasStarted) {
    return (
      <Button type="primary" size="large" onClick={handleStartConversation}>
        {t("home.startConversation")}
      </Button>
    );
  }

  return (
    <div className="home-chat">
      <div className="chat-container">
        <div className="chat-header">
          <div className="chat-title">{t("home.title")}</div>
          <div className="chat-subtitle">{t("home.subtitle")}</div>
        </div>

        <div className="chat-messages">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`chat-message-row ${
                msg.role === "ai" ? "ai-row" : "user-row"
              }`}
            >
              <div
                className={`chat-bubble ${
                  msg.role === "ai" ? "ai-bubble" : "user-bubble"
                }`}
              >
                <div className="chat-bubble-role">
                  {msg.role === "ai" ? t("roles.ai") : t("roles.user")}
                </div>
                <div className="chat-bubble-text">
                  {msg.text.split("\n").map((line, idx) => (
                    <p key={idx}>{line}</p>
                  ))}
                </div>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        <div className="chat-voice-bar">
          <div className="chat-voice-hint">
            {isRecording ? t("home.hintRecording") : t("home.hintIdle")}
          </div>
          <Button
            type={isRecording ? "default" : "primary"}
            danger={isRecording}
            onClick={isRecording ? handleStopRecording : handleStartRecording}
            loading={isProcessing}
            disabled={isProcessing}
          >
            {isRecording ? t("home.stopRecording") : t("home.startRecording")}
          </Button>
        </div>
      </div>
    </div>
  );
};

export default Home;
