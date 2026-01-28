import { createContext, useContext, useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import "dayjs/locale/en";
import { translations } from "./translations";

const LanguageContext = createContext(null);

const normalizeLang = (value) => (value === "en" ? "en" : "zh");

const getInitialLang = () => {
  const saved = window.localStorage.getItem("lang");
  if (saved) return normalizeLang(saved);
  const browserLang = navigator.language || "";
  return browserLang.toLowerCase().startsWith("zh") ? "zh" : "en";
};

const resolveKey = (obj, key) => {
  const parts = key.split(".");
  let current = obj;
  for (const part of parts) {
    if (!current || typeof current !== "object") return undefined;
    current = current[part];
  }
  return current;
};

export const LanguageProvider = ({ children }) => {
  const [lang, setLang] = useState(getInitialLang);

  useEffect(() => {
    window.localStorage.setItem("lang", lang);
    dayjs.locale(lang === "zh" ? "zh-cn" : "en");
  }, [lang]);

  const t = useMemo(() => {
    return (key) => {
      const table = translations[lang] || translations.zh;
      const value = resolveKey(table, key);
      if (typeof value === "string") return value;
      return key;
    };
  }, [lang]);

  const value = useMemo(() => {
    return {
      lang,
      setLang,
      toggleLang: () => setLang((prev) => (prev === "zh" ? "en" : "zh")),
      t,
    };
  }, [lang, t]);

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
};

export const useI18n = () => {
  const ctx = useContext(LanguageContext);
  if (!ctx) {
    throw new Error("useI18n must be used within LanguageProvider");
  }
  return ctx;
};
