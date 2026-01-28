import { Routes, Route, Link, useLocation } from "react-router-dom";
import routes from "./router/routes";
import { Button, Menu } from "antd";
import { useI18n } from "./i18n/LanguageContext.jsx";

const App = () => {
  const location = useLocation();
  const { t, toggleLang, lang } = useI18n();

  const items = routes
    .filter(r => r.labelKey)
    .map(route => ({
      key: route.path,
      label: <Link to={route.path}>{t(route.labelKey)}</Link>,
    }));

  return (
    <div className="app-root">
      <header className="header">
        <div className="header-menu">
          <Menu
            mode="horizontal"
            items={items}
            selectedKeys={[location.pathname]}
          />
        </div>
        <Button
          className="lang-toggle"
          type="default"
          onClick={toggleLang}
          aria-label={`switch language to ${lang === "zh" ? "English" : "Chinese"}`}
        >
          {t("nav.switchTo")}
        </Button>
      </header>

      <main className="content">
        <Routes>
          {routes.map(route => (
            <Route
              key={route.path}
              path={route.path}
              element={route.element}
            />
          ))}
        </Routes>
      </main>
    </div>
  );
};

export default App;
