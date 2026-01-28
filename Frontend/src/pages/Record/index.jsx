import { useI18n } from "../../i18n/LanguageContext.jsx";

function Record() {
  const { t } = useI18n();
  return <h1>{t("record.title")}</h1>;
}

export default Record;
