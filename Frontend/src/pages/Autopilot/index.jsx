import { useState, useRef } from "react";
import {
  Button,
  Input,
  Card,
  Collapse,
  Tag,
  Checkbox,
  message as AntMessage,
  Spin,
  Typography,
  Space,
  Divider,
} from "antd";
import {
  AudioOutlined,
  SendOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
} from "@ant-design/icons";
import { useI18n } from "../../i18n/LanguageContext.jsx";
import * as api from "../../utils/api";
import "./index.scss";

const { TextArea } = Input;
const { Text, Title } = Typography;

const STATUS_ICONS = {
  success: <CheckCircleOutlined style={{ color: "#52c41a" }} />,
  failed: <CloseCircleOutlined style={{ color: "#ff4d4f" }} />,
  blocked: <ExclamationCircleOutlined style={{ color: "#faad14" }} />,
  skipped: <CloseCircleOutlined style={{ color: "#999" }} />,
};

const escapeHtml = (str = "") =>
  str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");

const formatPlainTextAsHtml = (text = "") => {
  if (!text) return "";
  return text
    .trim()
    .split("\n\n")
    .map((block) => {
      const lines = block.split("\n").map((line) => escapeHtml(line));
      return `<p>${lines.join("<br/>")}</p>`;
    })
    .join("");
};

const Autopilot = () => {
  const { t, lang } = useI18n();
  const [inputText, setInputText] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [runResult, setRunResult] = useState(null);
  const [confirmResult, setConfirmResult] = useState(null);
  const [actionChecks, setActionChecks] = useState({});
  const [editedActions, setEditedActions] = useState([]);
  const [replyDraft, setReplyDraft] = useState({});
  const [rescheduleInputs, setRescheduleInputs] = useState({});
  const [rescheduleLoading, setRescheduleLoading] = useState({});
  const [rescheduleRecordingIndex, setRescheduleRecordingIndex] = useState(null);
  const mediaRecorderRef = useRef(null);
  const rescheduleRecorderRef = useRef(null);

  const handleRun = async (mode, audioB64 = null, overrideText = null) => {
    setIsProcessing(true);
    setRunResult(null);
    setConfirmResult(null);
    try {
      const body = { mode, locale: lang };
      if (mode === "audio") {
        body.audio_base64 = audioB64;
      } else {
        body.text = overrideText ?? inputText;
      }
      const res = await api.postAPI("/autopilot/run", body);
      const data = res?.data || res || {};
      setRunResult(data);
      setReplyDraft(data.reply_draft || {});
      // Init action checks - all checked by default
      const checks = {};
      (data.actions_preview || []).forEach((_, i) => {
        checks[i] = true;
      });
      setActionChecks(checks);
      setEditedActions(data.actions_preview || []);
    } catch (err) {
      console.error("Autopilot run error:", err);
      AntMessage.error(t("autopilot.runError"));
    } finally {
      setIsProcessing(false);
    }
  };

  const handleConfirm = async () => {
    if (!runResult?.run_id) return;
    setIsConfirming(true);
    try {
      const actions = editedActions.map((a, i) => ({
        ...a,
        confirmed: !!actionChecks[i],
        skip: !actionChecks[i],
      }));
      const res = await api.postAPI("/autopilot/confirm", {
        run_id: runResult.run_id,
        actions,
      });
      const data = res?.data || res || {};
      setConfirmResult(data);
      const results = data?.results || [];
      const calendarFailed = results.some(
        (r) => r.action_type === "create_meeting" && (r.status === "failed" || r.status === "blocked")
      );
      if (calendarFailed) {
        AntMessage.warning(t("autopilot.confirmCalendarFailed"));
      } else {
        AntMessage.success(t("autopilot.confirmSuccess"));
      }
    } catch (err) {
      console.error("Autopilot confirm error:", err);
      AntMessage.error(t("autopilot.confirmError"));
    } finally {
      setIsConfirming(false);
    }
  };

  const handleStartRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      AntMessage.error(t("errors.browserNotSupported"));
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: "audio/webm" });
        const reader = new FileReader();
        reader.onloadend = () => {
          const b64 = reader.result.split(",")[1];
          handleRun("audio", b64);
        };
        reader.readAsDataURL(blob);
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch {
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

  const appendToAnalyzeInput = (text) => {
    if (!text) return "";
    const next = inputText ? `${inputText}\n${text}` : text;
    setInputText(next);
    return next;
  };

  const buildRescheduleLine = (action, rawText) => {
    const payload = action?.payload || {};
    const title = payload.title || (lang === "zh" ? "日程安排" : "Meeting");
    const date = payload.date || "";
    const start = payload.start_time || "";
    const end = payload.end_time || "";
    const tz = "America/Toronto";
    if (lang === "zh") {
      return `改期更新：将「${title}」调整为 ${date} ${start}-${end}（时区：${tz}）。`.trim();
    }
    return `Reschedule update: move "${title}" to ${date} ${start}-${end} (Timezone: ${tz}).`.trim();
  };

  const handleReschedule = async (index, mode, audioB64 = null) => {
    const action = editedActions[index];
    if (!action) return;
    setRescheduleLoading((prev) => ({ ...prev, [index]: true }));
    try {
      const body = { mode, locale: lang, action };
      if (mode === "audio") {
        body.audio_base64 = audioB64;
      } else {
        body.text = (rescheduleInputs[index] || "").trim();
      }
      const res = await api.postAPI("/autopilot/adjust-time", body);
      const data = res?.data || res || {};
      const rawText = (data.user_text || body.text || "").trim();
      const normalizedLine = data.action ? buildRescheduleLine(data.action, rawText) : rawText;
      if (normalizedLine) {
        const nextInput = appendToAnalyzeInput(normalizedLine);
        setRescheduleInputs((prev) => ({ ...prev, [index]: "" }));
        await handleRun("text", null, nextInput);
      }
    } catch (err) {
      console.error("Reschedule error:", err);
      AntMessage.error(t("autopilot.rescheduleError"));
    } finally {
      setRescheduleLoading((prev) => ({ ...prev, [index]: false }));
    }
  };

  const handleRescheduleStartRecording = async (index) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      AntMessage.error(t("errors.browserNotSupported"));
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: "audio/webm" });
        const reader = new FileReader();
        reader.onloadend = () => {
          const b64 = reader.result.split(",")[1];
          handleReschedule(index, "audio", b64);
        };
        reader.readAsDataURL(blob);
      };
      recorder.start();
      rescheduleRecorderRef.current = recorder;
      setRescheduleRecordingIndex(index);
    } catch {
      AntMessage.error(t("errors.micDenied"));
    }
  };

  const handleRescheduleStopRecording = () => {
    if (rescheduleRecorderRef.current) {
      rescheduleRecorderRef.current.stop();
      rescheduleRecorderRef.current = null;
      setRescheduleRecordingIndex(null);
    }
  };

  const handleActionPayloadEdit = (index, field, value) => {
    setEditedActions((prev) => {
      const copy = [...prev];
      copy[index] = {
        ...copy[index],
        payload: { ...copy[index].payload, [field]: value },
      };
      return copy;
    });
  };

  const anyChecked = Object.values(actionChecks).some(Boolean);

  return (
    <div className="autopilot-page">
      <Title level={3}>{t("autopilot.title")}</Title>
      <Text type="secondary">{t("autopilot.subtitle")}</Text>

      {/* Input Section */}
      <Card className="autopilot-input-card" style={{ marginTop: 16 }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <TextArea
            rows={4}
            placeholder={t("autopilot.inputPlaceholder")}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            disabled={isProcessing}
          />
          <Space>
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={() => handleRun("text")}
              loading={isProcessing}
              disabled={!inputText.trim() || isProcessing}
            >
              {t("autopilot.analyze")}
            </Button>
            <Button
              type={isRecording ? "default" : "primary"}
              danger={isRecording}
              icon={<AudioOutlined />}
              onClick={isRecording ? handleStopRecording : handleStartRecording}
              disabled={isProcessing}
            >
              {isRecording ? t("autopilot.stopRecording") : t("autopilot.startRecording")}
            </Button>
          </Space>
        </Space>
      </Card>

      {isProcessing && (
        <div className="autopilot-loading">
          <Spin size="large" tip={t("autopilot.processing")} />
        </div>
      )}

      {/* Results Section */}
      {runResult && !isProcessing && (
        <div className="autopilot-results">
          {/* Transcript */}
          {runResult.transcript && (
            <Card size="small" title={t("autopilot.transcript")} style={{ marginTop: 12 }}>
              <Text>{runResult.transcript}</Text>
            </Card>
          )}

          <div className="autopilot-columns">
            {/* Column 1: Structured JSON */}
            <Card size="small" title={t("autopilot.structuredData")} className="autopilot-col">
              <Collapse
                size="small"
                items={[
                  {
                    key: "json",
                    label: t("autopilot.viewJson"),
                    children: (
                      <pre className="autopilot-json">
                        {JSON.stringify(runResult.extracted, null, 2)}
                      </pre>
                    ),
                  },
                ]}
              />
              <div style={{ marginTop: 8 }}>
                <Text strong>{t("autopilot.intent")}: </Text>
                <Tag color="blue">{runResult.extracted?.intent}</Tag>
                {runResult.extracted?.urgency && (
                  <Tag color={runResult.extracted.urgency === "high" ? "red" : "orange"}>
                    {runResult.extracted.urgency}
                  </Tag>
                )}
              </div>
              <div style={{ marginTop: 4 }}>
                <Text strong>{t("autopilot.summary")}: </Text>
                <Text>{runResult.extracted?.summary}</Text>
              </div>
              {runResult.extracted?.follow_up_questions?.length > 0 && (
                <div style={{ marginTop: 4 }}>
                  <Text strong>{t("autopilot.followUp")}:</Text>
                  <ul>
                    {runResult.extracted.follow_up_questions.map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>

            {/* Column 2: Evidence + Reply Draft */}
            <Card size="small" title={t("autopilot.evidenceAndDraft")} className="autopilot-col">
              <Text strong>{t("autopilot.evidence")}:</Text>
              {runResult.evidence?.length > 0 ? (
                <div className="autopilot-evidence-list">
                  {runResult.evidence.map((e, i) => (
                    <div key={i} className="autopilot-evidence-item">
                      <Tag>{e.doc}#{e.chunk}</Tag>
                      <Text type="secondary"> score: {e.score}</Text>
                      <div className="autopilot-evidence-text">{e.text?.substring(0, 200)}...</div>
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary">{t("autopilot.noEvidence")}</Text>
              )}
              <Divider />
              <Text strong>{t("autopilot.replyDraft")}:</Text>
              <div className="autopilot-email-preview" style={{ marginTop: 8 }}>
                {(replyDraft.from || replyDraft.to || replyDraft.subject) && (
                  <div className="autopilot-email-meta">
                    {replyDraft.from && (
                      <div>
                        <Text strong>{t("autopilot.emailFrom")}:</Text>{" "}
                        <Text>{replyDraft.from}</Text>
                      </div>
                    )}
                    {replyDraft.to && (
                      <div>
                        <Text strong>{t("autopilot.emailTo")}:</Text>{" "}
                        <Text>{replyDraft.to}</Text>
                      </div>
                    )}
                    {replyDraft.subject && (
                      <div>
                        <Text strong>{t("autopilot.emailSubject")}:</Text>{" "}
                        <Text>{replyDraft.subject}</Text>
                      </div>
                    )}
                  </div>
                )}
                <div
                  className="autopilot-email-body"
                  dangerouslySetInnerHTML={{
                    __html: replyDraft.html || formatPlainTextAsHtml(replyDraft.text || ""),
                  }}
                />
              </div>
              {runResult.reply_draft?.citations?.length > 0 && (
                <div style={{ marginTop: 4 }}>
                  <Text type="secondary">
                    {t("autopilot.citations")}: {runResult.reply_draft.citations.join(", ")}
                  </Text>
                </div>
              )}
            </Card>

            {/* Column 3: Actions */}
            <Card size="small" title={t("autopilot.actions")} className="autopilot-col">
              {editedActions.map((action, i) => (
                <div key={i} className="autopilot-action-item">
                  <Checkbox
                    checked={!!actionChecks[i]}
                    onChange={(e) =>
                      setActionChecks((prev) => ({ ...prev, [i]: e.target.checked }))
                    }
                  >
                    <Tag
                      color={
                        action.action_type === "create_meeting"
                          ? "purple"
                          : action.action_type === "send_slack_summary"
                          ? "cyan"
                          : action.action_type === "create_ticket"
                          ? "orange"
                          : "default"
                      }
                    >
                      {action.action_type}
                    </Tag>
                    <Text type="secondary">
                      {" "}
                      confidence: {action.confidence}
                    </Text>
                  </Checkbox>
                  <div className="autopilot-action-preview">{action.preview}</div>
                  <Collapse
                    size="small"
                    items={[
                      {
                        key: `payload-${i}`,
                        label: t("autopilot.editPayload"),
                        children: (
                          <div className="autopilot-payload-fields">
                            {Object.entries(action.payload || {}).map(([k, v]) => (
                              <div key={k} className="autopilot-payload-field">
                                <Text strong>{k}: </Text>
                                <Input
                                  size="small"
                                  value={typeof v === "object" ? JSON.stringify(v) : String(v ?? "")}
                                  onChange={(e) => handleActionPayloadEdit(i, k, e.target.value)}
                                />
                              </div>
                            ))}
                          </div>
                        ),
                      },
                    ]}
                  />
                </div>
              ))}
              <Button
                type="primary"
                onClick={handleConfirm}
                loading={isConfirming}
                disabled={!anyChecked || isConfirming}
                style={{ marginTop: 12, width: "100%" }}
              >
                {t("autopilot.confirmAndRun")}
              </Button>
            </Card>
          </div>

          {/* Execution Results */}
          {confirmResult && (
            <Card
              size="small"
              title={t("autopilot.executionResults")}
              style={{ marginTop: 12 }}
            >
              {confirmResult.results?.map((r, i) => (
                <div key={i} className="autopilot-exec-result">
                  {STATUS_ICONS[r.status] || STATUS_ICONS.failed}
                  <Tag>{r.action_type}</Tag>
                  <Tag color={r.status === "success" ? "green" : r.status === "blocked" ? "gold" : "red"}>
                    {r.status}
                  </Tag>
                  {r.result?.summary && <Text>{r.result.summary}</Text>}
                  {r.result?.url && (
                    <a href={r.result.url} target="_blank" rel="noreferrer">
                      {r.result.url}
                    </a>
                  )}
                  {r.result?.error && <Text type="danger">{r.result.error}</Text>}
                  {r.result?.message && <Text>{r.result.message}</Text>}
                  {r.status === "blocked" && r.action_type === "create_meeting" && (
                    <div className="autopilot-reschedule">
                      <Input
                        placeholder={t("autopilot.reschedulePlaceholder")}
                        value={rescheduleInputs[i] || ""}
                        onChange={(e) =>
                          setRescheduleInputs((prev) => ({ ...prev, [i]: e.target.value }))
                        }
                        onPressEnter={(e) => {
                          e.preventDefault();
                          if ((rescheduleInputs[i] || "").trim()) {
                            handleReschedule(i, "text");
                          }
                        }}
                        disabled={rescheduleLoading[i]}
                      />
                      <Space>
                        <Button
                          type="primary"
                          onClick={() => handleReschedule(i, "text")}
                          loading={rescheduleLoading[i]}
                          disabled={!(rescheduleInputs[i] || "").trim()}
                        >
                          {t("autopilot.reschedule")}
                        </Button>
                        <Button
                          type={rescheduleRecordingIndex === i ? "default" : "primary"}
                          danger={rescheduleRecordingIndex === i}
                          icon={<AudioOutlined />}
                          onClick={
                            rescheduleRecordingIndex === i
                              ? handleRescheduleStopRecording
                              : () => handleRescheduleStartRecording(i)
                          }
                          disabled={rescheduleLoading[i]}
                        >
                          {rescheduleRecordingIndex === i
                            ? t("autopilot.stopRecording")
                            : t("autopilot.startRecording")}
                        </Button>
                      </Space>
                    </div>
                  )}
                </div>
              ))}
            </Card>
          )}
        </div>
      )}
    </div>
  );
};

export default Autopilot;
