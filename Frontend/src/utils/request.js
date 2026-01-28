import axios from "axios";
import { message as AntMessage } from "antd";

const baseURL = process.env.NODE_ENV === "development" ? "/api" : "";

const I18N = {
  zh: {
    status: {
      400: "错误请求",
      401: "未授权，请重新登录",
      403: "拒绝访问",
      404: "请求错误，未找到该资源",
      405: "请求方法未允许",
      408: "请求超时",
      500: "服务器端出错",
      501: "网络未实现",
      502: "网络错误",
      503: "服务不可用",
      504: "网络超时",
      505: "HTTP 版本不支持该请求",
    },
    timeout: "服务器响应超时，请刷新当前页",
    networkFail: "连接服务器失败",
    connectionError: "连接错误",
  },
  en: {
    status: {
      400: "Bad request",
      401: "Unauthorized. Please sign in again.",
      403: "Access denied",
      404: "Resource not found",
      405: "Method not allowed",
      408: "Request timeout",
      500: "Server error",
      501: "Not implemented",
      502: "Bad gateway",
      503: "Service unavailable",
      504: "Gateway timeout",
      505: "HTTP version not supported",
    },
    timeout: "Server response timed out. Please refresh the page.",
    networkFail: "Failed to connect to server",
    connectionError: "Connection error",
  },
};

const getLang = () => {
  try {
    const saved = window.localStorage.getItem("lang") || "";
    return saved.toLowerCase().startsWith("en") ? "en" : "zh";
  } catch {
    return "zh";
  }
};

const instance = axios.create({
  baseURL,
  timeout: 100000,
  headers: {
    "X-Custom-Header": "foobar",
  },
});

// 请求拦截器
instance.interceptors.request.use(
  (config) => config,
  (error) => Promise.reject(error),
);

// 响应拦截器
instance.interceptors.response.use(
  (response) => response,
  (error) => {
    const lang = getLang();
    const messages = I18N[lang] || I18N.zh;

    if (error && error.response) {
      // 公共错误处理
      switch (error.response.status) {
        case 400:
          error.message = messages.status[400];
          break;
        case 401:
          error.message = messages.status[401];
          break;
        case 403:
          error.message = messages.status[403];
          break;
        case 404:
          error.message = messages.status[404];
          window.location.href = "/NotFound";
          break;
        case 405:
          error.message = messages.status[405];
          break;
        case 408:
          error.message = messages.status[408];
          break;
        case 500:
          error.message = messages.status[500];
          break;
        case 501:
          error.message = messages.status[501];
          break;
        case 502:
          error.message = messages.status[502];
          break;
        case 503:
          error.message = messages.status[503];
          break;
        case 504:
          error.message = messages.status[504];
          break;
        case 505:
          error.message = messages.status[505];
          break;
        default:
          error.message = `${messages.connectionError} ${error.response.status}`;
      }
    } else if (JSON.stringify(error).includes("timeout")) {
      error.message = messages.timeout;
    } else {
      error.message = messages.networkFail;
    }

    AntMessage.error(error.message);
    return Promise.reject(error);
  },
);

export default instance;
