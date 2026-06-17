import { useEffect, useMemo, useState } from "react";
import { format, formatDistanceToNow, parseISO, subDays } from "date-fns";
import {
  Activity,
  CalendarRange,
  FileText,
  Gauge,
  History,
  LayoutDashboard,
  MessageSquare,
  Send,
  Settings,
  Sparkles,
  Stethoscope,
  TimerReset,
  Upload,
} from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type TimelineEvent = {
  event_id: string;
  id?: string;
  timestamp?: string;
  start_time?: string | null;
  end_time?: string | null;
  category: string;
  type?: string;
  event_time: string;
  title: string;
  summary?: string;
  metrics?: Record<string, unknown>;
  source?: string | null;
  record_key?: string;
  data_quality?: string;
  detail_json: Record<string, unknown>;
};

type HealthConfig = {
  stale_sync_threshold_minutes?: number;
  report_window_days?: number;
  timeline_window_days?: number;
  report_disclaimer?: string;
  llm_enabled?: boolean;
  llm_base_url?: string;
  llm_model?: string | null;
  llm_api_key_set?: boolean;
};

type DoctorVisitReport = {
  report_id: string;
  report_type: string;
  start_date: string;
  end_date: string;
  generated_at: string;
  mode: string;
  disclaimer: string;
  coverage: {
    summary_days_covered: number;
    sleep_sessions: number;
    workouts: number;
    missing_summary_days: string[];
  };
  data: Record<string, unknown>;
  narrative: string;
  highlights: string[];
  trend_notes: string[];
};

type DoctorVisitResponse = {
  status: string;
  mode: string;
  report: DoctorVisitReport;
  context?: Record<string, unknown>;
};

type AskResponse = {
  status: string;
  mode: string;
  question: string;
  answer: string;
  evidence: string[];
  report: {
    report_id: string;
    start_date: string;
    end_date: string;
    disclaimer: string;
  };
};

type DashboardState = {
  status: { status?: string; counts?: Record<string, number>; last_sync_at?: string | null } | null;
  overview: { cards?: { latest_day?: Record<string, unknown> }; daily_summaries?: Array<Record<string, unknown>> } | null;
  activity: { daily_summaries?: Array<Record<string, unknown>>; workouts?: Array<Record<string, unknown>>; interval_metrics?: Array<Record<string, unknown>> } | null;
  sleep: { sessions?: Array<Record<string, unknown>>; stages?: Array<Record<string, unknown>> } | null;
  vitals: { point_metrics?: Array<Record<string, unknown>> } | null;
  body: { point_metrics?: Array<Record<string, unknown>> } | null;
  timeline: { events?: Array<TimelineEvent> } | null;
  batches: { batches?: Array<Record<string, unknown>> } | null;
  config: HealthConfig | null;
  loading: boolean;
  source: "live" | "fixture";
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
const READ_TOKEN = import.meta.env.VITE_READ_TOKEN ?? "read-token";

function asNumber(value: unknown) {
  const result = Number(value ?? 0);
  return Number.isFinite(result) ? result : 0;
}

function formatNumber(value: unknown) {
  return asNumber(value).toLocaleString();
}

function formatDuration(minutes: unknown) {
  const value = asNumber(minutes);
  const hours = Math.floor(value / 60);
  const mins = Math.round(value % 60);
  return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
}

function minutesSinceIso(value: string | null | undefined) {
  if (!value) return null;
  const deltaMs = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(deltaMs)) return null;
  return Math.max(0, Math.round(deltaMs / 60000));
}

function formatSignedNumber(value: number, unit = "") {
  const prefix = value > 0 ? "+" : "";
  const suffix = unit ? ` ${unit}` : "";
  return `${prefix}${value.toLocaleString()}${suffix}`;
}

function metricLabel(value: unknown) {
  const key = String(value ?? "");
  const labels: Record<string, string> = {
    active_calories: "Active Calories",
    blood_glucose: "Blood Glucose",
    blood_pressure: "Blood Pressure",
    body_fat: "Body Fat",
    body_temperature: "Body Temperature",
    body_water_mass: "Body Water",
    bone_mass: "Bone Mass",
    distance: "Distance",
    heart_rate: "Heart Rate",
    heart_rate_variability: "HRV",
    height: "Height",
    lean_body_mass: "Lean Body Mass",
    oxygen_saturation: "SpO2",
    respiratory_rate: "Respiratory Rate",
    resting_heart_rate: "Resting HR",
    steps: "Steps",
    total_calories: "Total Calories",
    weight: "Weight",
  };
  return labels[key] ?? key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatMetricValue(row: Record<string, unknown> | undefined) {
  if (!row) return "n/a";
  if (row.numeric_value !== null && row.numeric_value !== undefined) {
    return `${formatNumber(row.numeric_value)}${row.unit ? ` ${String(row.unit)}` : ""}`;
  }
  return String(row.text_value ?? "n/a");
}

function defaultRange(endDate: string | undefined, windowDays: number) {
  const end = endDate ? parseISO(`${endDate.slice(0, 10)}T00:00:00Z`) : new Date();
  const start = subDays(end, Math.max(0, windowDays - 1));
  return {
    start: format(start, "yyyy-MM-dd"),
    end: format(end, "yyyy-MM-dd"),
  };
}

async function apiPost(path: string, body: unknown) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${READ_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`POST ${path} failed: ${response.status}`);
  }
  return response;
}

async function apiPut(path: string, body: unknown) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${READ_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`PUT ${path} failed: ${response.status}`);
  }
  return response.json();
}

async function readSseText(response: Response): Promise<string> {
  const reader = response.body?.getReader();
  if (!reader) {
    return response.text();
  }
  const decoder = new TextDecoder();
  let buffer = "";
  let collected = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let splitIndex = buffer.indexOf("\n\n");
    while (splitIndex !== -1) {
      const block = buffer.slice(0, splitIndex);
      buffer = buffer.slice(splitIndex + 2);
      const eventType = block
        .split("\n")
        .find((line) => line.startsWith("event: "))
        ?.slice("event: ".length);
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice("data: ".length))
        .join("\n");
      if (eventType === "token") {
        try {
          collected += JSON.parse(data) as string;
        } catch {
          collected += data;
        }
      } else if (eventType === "done") {
        try {
          const parsed = JSON.parse(data) as { report?: DoctorVisitReport; answer?: string };
          if (parsed.report?.narrative) {
            collected = parsed.report.narrative;
          }
          if (parsed.answer) {
            collected = parsed.answer;
          }
        } catch {
          collected = collected || data;
        }
      }
      splitIndex = buffer.indexOf("\n\n");
    }
  }

  return collected.trim();
}

function _buildFixtureDashboard(): Omit<DashboardState, "loading"> {
  const daily = [
    { summary_date: "2026-06-11", steps: 8420, active_minutes: 42, sleep_minutes: 470, workouts: 1 },
    { summary_date: "2026-06-10", steps: 7600, active_minutes: 35, sleep_minutes: 0, workouts: 0 },
    { summary_date: "2026-06-09", steps: 10110, active_minutes: 51, sleep_minutes: 460, workouts: 1 },
  ];
  const timeline = [
    {
      event_id: "workout:2026-06-11T17:00:00Z:Workout recorded",
      category: "activity",
      event_time: "2026-06-11T17:00:00Z",
      title: "Workout recorded",
      detail_json: {
        workout_key: "health_connect:walking:2026-06-11T17:00:00Z:2026-06-11T17:42:00Z:42",
        activity_type: "walking",
        duration_minutes: 42,
        calories: 260,
      },
    },
    {
      event_id: "sleep:2026-06-10T22:30:00Z:Sleep session recorded",
      category: "sleep",
      event_time: "2026-06-10T22:30:00Z",
      title: "Sleep session recorded",
      detail_json: {
        session_key: "health_connect:sleep_session:2026-06-10T22:30:00Z:2026-06-11T06:20:00Z",
        duration_minutes: 470,
        efficiency_pct: 95,
      },
    },
    {
      event_id: "point:2026-06-11T08:00:00Z:Heart rate recorded",
      category: "vitals",
      event_time: "2026-06-11T08:00:00Z",
      title: "Heart rate recorded",
      detail_json: {
        record_key: "health_connect:heart_rate:2026-06-11T08:00:00Z",
        metric_type: "heart_rate",
        numeric_value: 61,
        unit: "bpm",
      },
    },
  ];

  return {
    status: {
      status: "ok",
      counts: {
        ingest_batches: 1,
        metric_intervals: 4,
        metric_points: 8,
        sleep_sessions: 1,
        sleep_stages: 1,
        workouts: 1,
      },
      last_sync_at: "2026-06-11T08:00:00Z",
    },
    batches: {
      batches: [
        {
          batch_id: "batch_fixture_1",
          source: "health_connect",
          received_at: "2026-06-11T08:00:00Z",
          processed_count: 8,
          error_count: 0,
          status: "completed",
          notes: "1.2.1",
          payload_json: {
            timestamp: "2026-06-11T08:00:00Z",
            source: "health_connect",
          },
        },
      ],
    },
    config: {
      stale_sync_threshold_minutes: 180,
      report_window_days: 7,
      timeline_window_days: 14,
      report_disclaimer: "This is a non-diagnostic summary of trends from your own health data.",
      llm_enabled: false,
      llm_model: null,
    },
    overview: {
      cards: {
        latest_day: daily[0],
      },
      daily_summaries: daily,
    },
    activity: {
      daily_summaries: daily,
      workouts: [
        {
          workout_key: "health_connect:walking:2026-06-11T17:00:00Z:2026-06-11T17:42:00Z:42",
          activity_type: "walking",
          start_time: "2026-06-11T17:00:00Z",
          end_time: "2026-06-11T17:42:00Z",
          duration_minutes: 42,
          calories: 260,
          avg_hr: 112,
        },
      ],
      interval_metrics: [
        {
          record_key: "health_connect:steps:2026-06-11T00:00:00Z:2026-06-11T23:59:59Z",
          metric_type: "steps",
          start_time: "2026-06-11T00:00:00Z",
          end_time: "2026-06-11T23:59:59Z",
          numeric_value: 8420,
          unit: "count",
        },
      ],
    },
    sleep: {
      sessions: [
        {
          session_key: "health_connect:sleep_session:2026-06-10T22:30:00Z:2026-06-11T06:20:00Z",
          start_time: "2026-06-10T22:30:00Z",
          end_time: "2026-06-11T06:20:00Z",
          duration_minutes: 470,
          efficiency_pct: 95,
        },
      ],
      stages: [
        {
          stage_key: "health_connect:sleep_session:2026-06-10T22:30:00Z:2026-06-11T06:20:00Z:light:2026-06-10T22:30:00Z:2026-06-11T00:20:00Z",
          session_key: "health_connect:sleep_session:2026-06-10T22:30:00Z:2026-06-11T06:20:00Z",
          stage_type: "light",
          start_time: "2026-06-10T22:30:00Z",
          end_time: "2026-06-11T00:20:00Z",
          duration_seconds: 6600,
        },
      ],
    },
    vitals: {
      point_metrics: [
        {
          record_key: "health_connect:resting_heart_rate:2026-06-11T07:45:00Z",
          metric_type: "resting_heart_rate",
          recorded_at: "2026-06-11T07:45:00Z",
          numeric_value: 59,
          unit: "bpm",
        },
        {
          record_key: "health_connect:heart_rate:2026-06-11T08:00:00Z",
          metric_type: "heart_rate",
          recorded_at: "2026-06-11T08:00:00Z",
          numeric_value: 61,
          unit: "bpm",
        },
        {
          record_key: "health_connect:heart_rate_variability:2026-06-11T07:45:00Z",
          metric_type: "heart_rate_variability",
          recorded_at: "2026-06-11T07:45:00Z",
          numeric_value: 48,
          unit: "ms",
        },
      ],
    },
    body: {
      point_metrics: [
        { record_key: "health_connect:weight:2026-06-11T07:30:00Z", metric_type: "weight", recorded_at: "2026-06-11T07:30:00Z", numeric_value: 72.4, unit: "kg" },
        { record_key: "health_connect:height:2026-06-11T07:30:00Z", metric_type: "height", recorded_at: "2026-06-11T07:30:00Z", numeric_value: 178, unit: "cm" },
        { record_key: "health_connect:body_fat:2026-06-11T07:30:00Z", metric_type: "body_fat", recorded_at: "2026-06-11T07:30:00Z", numeric_value: 18.4, unit: "%" },
      ],
    },
    timeline: {
      events: timeline,
    },
    loading: false,
    source: "fixture",
  };
}

const FIXTURE_DASHBOARD = _buildFixtureDashboard();

async function apiGet(path: string) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Authorization: `Bearer ${READ_TOKEN}`,
    },
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
  }
  return response.json();
}

async function loadDashboardData(): Promise<DashboardState> {
  try {
    const [status, overview, activity, sleep, vitals, body, timeline, batches, config] = await Promise.all([
      apiGet("/health/status"),
      apiGet("/health/overview"),
      apiGet("/health/activity"),
      apiGet("/health/sleep"),
      apiGet("/health/vitals"),
      apiGet("/health/body"),
      apiGet("/health/timeline?days=14"),
      apiGet("/health/batches?limit=10"),
      apiGet("/health/config"),
    ]);

    return {
      status,
      overview,
      activity,
      sleep,
      vitals,
      body,
      timeline,
      batches,
      config,
      loading: false,
      source: "live",
    };
  } catch {
    return {
      ...FIXTURE_DASHBOARD,
      loading: false,
    };
  }
}

function App() {
  const [data, setData] = useState<DashboardState>({
    status: null,
    overview: null,
    activity: null,
    sleep: null,
    vitals: null,
    body: null,
    timeline: null,
    batches: null,
    config: null,
    loading: true,
    source: "fixture",
  });
  const [activeTab, setActiveTab] = useState("Overview");
  const [reportStartDate, setReportStartDate] = useState("");
  const [reportEndDate, setReportEndDate] = useState("");
  const [reportStream, setReportStream] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState("");
  const [reportOutput, setReportOutput] = useState("");
  const [askQuestion, setAskQuestion] = useState("How did my sleep and activity look?");
  const [askStartDate, setAskStartDate] = useState("");
  const [askEndDate, setAskEndDate] = useState("");
  const [askLoading, setAskLoading] = useState(false);
  const [askError, setAskError] = useState("");
  const [askOutput, setAskOutput] = useState("");
  const [settingsForm, setSettingsForm] = useState({ llm_base_url: "", llm_model: "", llm_api_key: "" });
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [settingsSaved, setSettingsSaved] = useState(false);

  useEffect(() => {
    let mounted = true;
    loadDashboardData().then((next) => {
      if (mounted) {
        setData(next);
        setSettingsForm({
          llm_base_url: next.config?.llm_base_url ?? "",
          llm_model: next.config?.llm_model ?? "",
          llm_api_key: "",
        });
      }
    });
    return () => {
      mounted = false;
    };
  }, []);

  const counts = data.status?.counts ?? {};
  const batches = data.batches?.batches ?? FIXTURE_DASHBOARD.batches?.batches ?? [];
  const latestSummary = data.overview?.cards?.latest_day ?? FIXTURE_DASHBOARD.overview?.cards?.latest_day;
  const dailySummaries = data.overview?.daily_summaries ?? FIXTURE_DASHBOARD.overview?.daily_summaries ?? [];
  const latestBatch = batches[0] ?? FIXTURE_DASHBOARD.batches?.batches?.[0];
  const previousSummary = dailySummaries[1] ?? null;
  const config = data.config ?? FIXTURE_DASHBOARD.config;
  const reportWindowDays = config?.report_window_days ?? 7;
  const lastSyncAt = data.status?.last_sync_at ?? null;
  const lastSyncMinutes = minutesSinceIso(lastSyncAt);
  const syncThreshold = config?.stale_sync_threshold_minutes ?? 180;
  const syncIsStale = data.source === "live" && lastSyncMinutes !== null && lastSyncMinutes > syncThreshold;
  const latestSummaryDate = String(latestSummary?.summary_date ?? "");
  const defaultReportRange = useMemo(
    () => defaultRange(latestSummaryDate || undefined, reportWindowDays),
    [latestSummaryDate, reportWindowDays],
  );
  const reportStartValue = reportStartDate || defaultReportRange.start;
  const reportEndValue = reportEndDate || defaultReportRange.end;
  const askStartValue = askStartDate || defaultReportRange.start;
  const askEndValue = askEndDate || defaultReportRange.end;
  const trendData = useMemo(
    () =>
      (dailySummaries.length ? dailySummaries : []).slice().reverse().map((row) => ({
        day: format(new Date(`${String(row.summary_date)}T00:00:00Z`), "EEE"),
        steps: Number(row.steps ?? 0),
        sleep: Number(row.sleep_minutes ?? 0),
        workouts: Number(row.workouts ?? 0),
      })),
    [dailySummaries],
  );
  const weeklyRows = dailySummaries.slice(0, 7);
  const weeklyTotals = weeklyRows.reduce(
    (acc, row) => ({
      steps: acc.steps + Number(row.steps ?? 0),
      sleep: acc.sleep + Number(row.sleep_minutes ?? 0),
      workouts: acc.workouts + Number(row.workouts ?? 0),
      active: acc.active + Number(row.active_minutes ?? 0),
    }),
    { steps: 0, sleep: 0, workouts: 0, active: 0 },
  );
  const weeklyAverageSteps = weeklyRows.length ? Math.round(weeklyTotals.steps / weeklyRows.length) : 0;
  const weeklyAverageSleep = weeklyRows.length ? Math.round(weeklyTotals.sleep / weeklyRows.length) : 0;
const timelineEvents = (data.timeline?.events ?? FIXTURE_DASHBOARD.timeline?.events ?? []).map((event) => ({
    ...event,
    detail_json: event.detail_json,
  }));

  const activityWorkouts = data.activity?.workouts ?? FIXTURE_DASHBOARD.activity?.workouts ?? [];
  const activityDailyRows = data.activity?.daily_summaries ?? FIXTURE_DASHBOARD.activity?.daily_summaries ?? [];
  const activityIntervals = data.activity?.interval_metrics ?? FIXTURE_DASHBOARD.activity?.interval_metrics ?? [];
  const recentStepIntervals = activityIntervals.filter((row) => row.metric_type === "steps").slice(0, 8);
  const sleepSessions = data.sleep?.sessions ?? FIXTURE_DASHBOARD.sleep?.sessions ?? [];
  const uniqueSleepSessions = Array.from(
    new Map(
      sleepSessions.map((row) => [
        `${String(row.start_time).slice(0, 16)}:${String(row.end_time).slice(0, 16)}`,
        row,
      ]),
    ).values(),
  );
  const sleepStages = data.sleep?.stages ?? FIXTURE_DASHBOARD.sleep?.stages ?? [];
  const stageTotals = sleepStages.reduce<Record<string, number>>((acc, row) => {
    const label = String(row.stage_type ?? "unknown");
    acc[label] = (acc[label] ?? 0) + Number(row.duration_seconds ?? 0);
    return acc;
  }, {});
  const vitalsRows = data.vitals?.point_metrics ?? FIXTURE_DASHBOARD.vitals?.point_metrics ?? [];
  const vitalsByType = Array.from(
    vitalsRows.reduce<Map<string, Array<Record<string, unknown>>>>((acc, row) => {
      const key = String(row.metric_type ?? "metric");
      acc.set(key, [...(acc.get(key) ?? []), row]);
      return acc;
    }, new Map()).entries(),
  );
  const vitalsLatestTimes = vitalsRows
    .map((row) => new Date(String(row.recorded_at)).getTime())
    .filter((value) => Number.isFinite(value));
  const vitalsWindowLabel = vitalsLatestTimes.length
    ? `Recent readings through ${format(new Date(Math.max(...vitalsLatestTimes)), "MMM d, p")}`
    : "No recent vitals";
  const bodyRows = data.body?.point_metrics ?? FIXTURE_DASHBOARD.body?.point_metrics ?? [];
  const bodyByType = Array.from(
    bodyRows.reduce<Map<string, Array<Record<string, unknown>>>>((acc, row) => {
      const key = String(row.metric_type ?? "metric");
      acc.set(key, [...(acc.get(key) ?? []), row]);
      return acc;
    }, new Map()).entries(),
  );

  const restingHr = (data.vitals?.point_metrics ?? []).find((row) => row.metric_type === "resting_heart_rate")?.numeric_value
    ?? FIXTURE_DASHBOARD.vitals?.point_metrics?.find((row) => row.metric_type === "resting_heart_rate")?.numeric_value
    ?? 0;

  const snapshotRows = [
    { label: "Date", value: String(latestSummary?.summary_date ?? "n/a") },
    { label: "Steps", value: formatNumber(latestSummary?.steps) },
    { label: "Active", value: `${formatNumber(latestSummary?.active_minutes)}m` },
    { label: "Sleep", value: formatDuration(latestSummary?.sleep_minutes) },
    { label: "Workouts", value: formatNumber(latestSummary?.workouts) },
    { label: "Resting HR", value: `${formatNumber(restingHr)} bpm` },
  ];

  const ingestIssues = [
    syncIsStale ? `Sync is stale by ${lastSyncMinutes} minutes.` : null,
    latestBatch && Number(latestBatch.error_count ?? 0) > 0
      ? `Latest batch has ${formatNumber(latestBatch.error_count)} error${Number(latestBatch.error_count) === 1 ? "" : "s"}.`
      : null,
    !Number(latestSummary?.sleep_minutes ?? 0) ? "No sleep duration captured on the latest summary day." : null,
    !Number(latestSummary?.workouts ?? 0) ? "No workout captured on the latest summary day." : null,
  ].filter(Boolean) as string[];

  const summaryComparisons = [
    {
      label: "Steps",
      value: formatSignedNumber(Number(latestSummary?.steps ?? 0) - Number(previousSummary?.steps ?? 0), "vs prev day"),
    },
    {
      label: "Sleep",
      value: formatSignedNumber(Number(latestSummary?.sleep_minutes ?? 0) - Number(previousSummary?.sleep_minutes ?? 0), "min vs prev day"),
    },
    {
      label: "Workouts",
      value: formatSignedNumber(Number(latestSummary?.workouts ?? 0) - Number(previousSummary?.workouts ?? 0), "vs prev day"),
    },
  ];

  const cards = [
    {
      label: "Latest steps",
      value: formatNumber(latestSummary?.steps),
      delta: `7d total ${formatNumber(weeklyTotals.steps)}`,
      icon: Activity,
    },
    {
      label: "Sleep last night",
      value: formatDuration(latestSummary?.sleep_minutes),
      delta: `${formatNumber(counts.sleep_sessions ?? 1)} sessions`,
      icon: TimerReset,
    },
    {
      label: "Resting HR",
      value: `${formatNumber(restingHr)} bpm`,
      delta: counts.metric_points ? "ready" : "fixture",
      icon: Gauge,
    },
    {
      label: "Last sync",
      value: lastSyncAt ? formatDistanceToNow(new Date(lastSyncAt), { addSuffix: true }) : "fixture preview",
      delta: syncIsStale ? `stale by ${lastSyncMinutes}m` : data.source === "live" ? data.status?.status ?? "ok" : "fixture",
      icon: Upload,
    },
  ];

  const tabGroups = [
    [LayoutDashboard, "Overview"],
    [History, "Timeline"],
    [CalendarRange, "Activity"],
    [TimerReset, "Sleep"],
    [Gauge, "Vitals"],
    [Stethoscope, "Body"],
    [Upload, "Data"],
    [FileText, "Reports"],
    [MessageSquare, "Ask"],
    [Settings, "Settings"],
  ] as const;

  async function copyReportOutput() {
    if (!reportOutput) return;
    await navigator.clipboard.writeText(reportOutput);
  }

  function downloadReportMarkdown() {
    if (!reportOutput) return;
    const blob = new Blob([reportOutput], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `healthquery-report-${reportStartValue}-to-${reportEndValue}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function handleReportSubmit() {
    setReportLoading(true);
    setReportError("");
    setReportOutput("");
    try {
      const response = await apiPost("/reports/doctor-visit", {
        start_date: reportStartValue,
        end_date: reportEndValue,
        stream: reportStream && Boolean(config?.llm_enabled),
      });
      if (reportStream && Boolean(config?.llm_enabled)) {
        setReportOutput(await readSseText(response));
        return;
      }
      const payload = (await response.json()) as DoctorVisitResponse;
      const report = payload.report;
      const lines = [
        report.narrative,
        "",
        "Highlights:",
        ...report.highlights.map((item) => `- ${item}`),
        "",
        "Trend notes:",
        ...report.trend_notes.map((item) => `- ${item}`),
        "",
        `Coverage: ${report.coverage.summary_days_covered} summary days, ${report.coverage.sleep_sessions} sleep sessions, ${report.coverage.workouts} workouts.`,
        report.disclaimer,
      ];
      setReportOutput(lines.join("\n"));
    } catch (error) {
      setReportError(error instanceof Error ? error.message : "Report generation failed");
    } finally {
      setReportLoading(false);
    }
  }

  async function handleAskSubmit() {
    setAskLoading(true);
    setAskError("");
    setAskOutput("");
    try {
      const response = await apiPost("/reports/ask", {
        question: askQuestion,
        start_date: askStartValue,
        end_date: askEndValue,
      });
      const payload = (await response.json()) as AskResponse;
      const isLlm = payload.mode === "llm";
      const parts = [payload.answer];
      if (!isLlm) {
        parts.push("", `Evidence: ${payload.evidence.length ? payload.evidence.join("; ") : "No specific evidence returned."}`, payload.report.disclaimer);
      }
      setAskOutput(parts.join("\n"));
    } catch (error) {
      setAskError(error instanceof Error ? error.message : "Ask request failed");
    } finally {
      setAskLoading(false);
    }
  }

  function renderSelectedTabContent() {
    if (activeTab === "Overview") {
      return (
        <div className="space-y-6">
          <div className="grid gap-6 xl:grid-cols-[1.45fr_1fr]">
            <div className="rounded-md border border-border bg-background/60 p-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="text-sm font-medium">7-day trend</div>
                  <p className="mt-1 text-sm text-muted-foreground">Daily steps with recent summary context.</p>
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="pill"><LayoutDashboard className="h-3.5 w-3.5" /> Steps</span>
                  <span className="pill"><Sparkles className="h-3.5 w-3.5" /> Sleep</span>
                </div>
              </div>
              <div className="mt-5 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={trendData.length ? trendData : [
                    { day: "Mon", steps: 8420, sleep: 470, workouts: 1 },
                    { day: "Tue", steps: 7600, sleep: 0, workouts: 0 },
                    { day: "Wed", steps: 10110, sleep: 460, workouts: 1 },
                  ]}>
                    <defs>
                      <linearGradient id="stepsFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0.03} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="day" stroke="hsl(var(--muted-foreground))" tickLine={false} axisLine={false} />
                    <YAxis stroke="hsl(var(--muted-foreground))" tickLine={false} axisLine={false} />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(var(--card))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: 8,
                      }}
                    />
                    <Area type="monotone" dataKey="steps" stroke="hsl(var(--primary))" fill="url(#stepsFill)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="space-y-4">
              <div className="rounded-md border border-border bg-background/60 p-4">
                <div className="text-sm font-medium">Latest snapshot</div>
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  {snapshotRows.map((row) => (
                    <div key={row.label} className="rounded-md border border-border bg-background/60 px-3 py-2">
                      <div className="text-xs uppercase text-muted-foreground">{row.label}</div>
                      <div className="mt-1 text-sm font-medium">{row.value}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-3 text-xs text-muted-foreground">
                  {data.source === "live" ? "Live data" : "Fixture preview"} · Last sync{" "}
                  {lastSyncAt ? formatDistanceToNow(new Date(lastSyncAt), { addSuffix: true }) : "fixture"}
                </div>
              </div>

              <div className="rounded-md border border-border bg-background/60 p-4">
                <div className="text-sm font-medium">Attention needed</div>
                <div className="mt-3 space-y-3">
                  {ingestIssues.length ? (
                    ingestIssues.map((issue) => (
                      <div key={issue} className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning-foreground">
                        {issue}
                      </div>
                    ))
                  ) : (
                    <div className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm text-muted-foreground">
                      No immediate issues detected.
                    </div>
                  )}
                  <div className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-medium">Latest batch</div>
                      <span className="pill">{String(latestBatch?.status ?? "unknown")}</span>
                    </div>
                    <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                      <div>Source: {String(latestBatch?.source ?? "n/a")}</div>
                      <div>Received: {latestBatch?.received_at ? format(new Date(String(latestBatch.received_at)), "MMM d, p") : "n/a"}</div>
                      <div>Processed: {formatNumber(latestBatch?.processed_count)}</div>
                      <div>Errors: {formatNumber(latestBatch?.error_count)}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-6 xl:grid-cols-[1.2fr_1fr]">
            <div className="rounded-md border border-border bg-background/60 p-4">
              <div className="text-sm font-medium">Weekly summary</div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  { label: "Average steps", value: formatNumber(weeklyAverageSteps) },
                  { label: "Average sleep", value: formatDuration(weeklyAverageSleep) },
                  { label: "Workouts", value: formatNumber(weeklyTotals.workouts) },
                  { label: "Active minutes", value: formatNumber(weeklyTotals.active) },
                ].map((item) => (
                  <div key={item.label} className="rounded-md border border-border bg-background/60 px-3 py-2">
                    <div className="text-xs uppercase text-muted-foreground">{item.label}</div>
                    <div className="mt-1 text-lg font-semibold">{item.value}</div>
                  </div>
                ))}
              </div>
              <div className="mt-4 space-y-2">
                {weeklyRows.slice(0, 4).map((row) => (
                  <div key={String(row.summary_date)} className="flex items-center justify-between gap-3 rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                    <div>
                      <div className="font-medium">{String(row.summary_date)}</div>
                      <div className="text-xs text-muted-foreground">
                        {formatNumber(row.steps)} steps · {formatDuration(row.sleep_minutes)} sleep · {formatNumber(row.workouts)} workouts
                      </div>
                    </div>
                    <span className="pill">{formatNumber(row.active_minutes)} active min</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-border bg-background/60 p-4">
              <div className="text-sm font-medium">Day over day</div>
              <div className="mt-4 space-y-3">
                {summaryComparisons.map((row) => (
                  <div key={row.label} className="rounded-md border border-border bg-background/60 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-medium">{row.label}</div>
                      <span className="pill">{row.value}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === "Timeline") {
      return (
        <div className="space-y-3">
          {timelineEvents.map((event) => (
            <div key={event.event_id} className="rounded-md border border-border bg-background/60 px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{event.title}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {format(new Date(event.event_time), "MMM d, p")}
                    {event.source ? ` · ${event.source}` : ""}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="pill">{event.category}</span>
                  {event.type ? <span className="pill">{event.type}</span> : null}
                  {event.data_quality ? <span className="pill">{event.data_quality}</span> : null}
                </div>
              </div>
              {event.summary ? <p className="mt-2 text-sm text-muted-foreground">{event.summary}</p> : null}
              {event.metrics && Object.keys(event.metrics).length ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {Object.entries(event.metrics).map(([key, value]) => (
                    <span key={key} className="pill">
                      {key}: {String(value)}
                    </span>
                  ))}
                </div>
              ) : null}
              <details className="mt-3 text-xs text-muted-foreground">
                <summary className="cursor-pointer">Record details</summary>
                <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-muted/50 p-2">
                  {JSON.stringify(event.detail_json, null, 2)}
                </pre>
              </details>
            </div>
          ))}
        </div>
      );
    }

    if (activeTab === "Activity") {
      return (
        <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
          <div>
            <div className="text-sm font-medium">Recent workouts</div>
            {activityWorkouts.length ? (
              <div className="mt-2 space-y-2">
                {activityWorkouts.slice(0, 5).map((row) => (
                  <div key={row.workout_key as string} className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-medium">{metricLabel(row.activity_type)}</div>
                      <span className="pill">{formatDuration(row.duration_minutes)}</span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {format(new Date(String(row.start_time)), "MMM d, p")} to {format(new Date(String(row.end_time)), "p")}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-2 rounded-md border border-border bg-background/60 px-3 py-3 text-sm text-muted-foreground">
                No workouts synced yet.
              </div>
            )}
          </div>
          <div>
            <div className="text-sm font-medium">Daily steps</div>
            <div className="mt-2 space-y-2">
              {activityDailyRows.slice(0, 7).map((row) => (
                <div key={row.summary_date as string} className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div>{String(row.summary_date)}</div>
                    <span className="pill">{formatNumber(row.steps)} steps</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {formatNumber(row.active_minutes)} active minutes · {formatNumber(row.workouts)} workouts
                  </div>
                </div>
              ))}
              {!activityDailyRows.length && recentStepIntervals.map((row) => (
                <div key={row.record_key as string} className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div>{format(new Date(String(row.start_time)), "MMM d")}</div>
                    <span className="pill">{formatNumber(row.numeric_value)} steps</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === "Sleep") {
      return (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <div className="text-sm font-medium">Sleep sessions</div>
            <div className="mt-2 space-y-2">
              {uniqueSleepSessions.map((row) => (
                <div key={row.session_key as string} className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div>{format(new Date(String(row.start_time)), "MMM d")}</div>
                    <span className="pill">{formatDuration(row.duration_minutes)}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">Efficiency {formatNumber(row.efficiency_pct)}%</div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="text-sm font-medium">Stage breakdown</div>
            <div className="mt-2 space-y-2">
              {Object.entries(stageTotals).map(([stage, seconds]) => (
                <div key={stage} className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div className="font-medium">{metricLabel(stage)}</div>
                    <span className="pill">{formatDuration(seconds / 60)}</span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${Math.min(100, Math.max(4, (seconds / Math.max(...Object.values(stageTotals), 1)) * 100))}%` }}
                    />
                  </div>
                </div>
              ))}
              {!Object.keys(stageTotals).length ? (
                <div className="rounded-md border border-border bg-background/60 px-3 py-3 text-sm text-muted-foreground">
                  No sleep stages synced yet.
                </div>
              ) : null}
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === "Vitals") {
      return (
        <div className="space-y-4">
          <div className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm text-muted-foreground">
            Cards show the latest reading for each metric. The mini bars and stats summarize the most recent readings currently loaded.
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {vitalsByType.map(([type, rows]) => {
              const latest = rows[0];
              const numericRows = rows.slice(0, 12).reverse().filter((row) => row.numeric_value !== null && row.numeric_value !== undefined);
              const values = numericRows.map((row) => Number(row.numeric_value ?? 0));
              const maxValue = Math.max(...values, 1);
              const minValue = values.length ? Math.min(...values) : 0;
              const avgValue = values.length ? Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 10) / 10 : 0;
              const unit = String(latest?.unit ?? "");
              return (
                <div key={type} className="rounded-md border border-border bg-background/60 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm text-muted-foreground">{metricLabel(type)}</div>
                    <span className="pill">Latest</span>
                  </div>
                  <div className="mt-2 text-2xl font-semibold">{formatMetricValue(latest)}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    Measured {latest?.recorded_at ? format(new Date(String(latest.recorded_at)), "MMM d, p") : "No reading"}
                  </div>
                  <div className="mt-4 flex h-10 items-end gap-1" title={vitalsWindowLabel}>
                    {numericRows.map((row) => (
                      <div
                        key={String(row.record_key)}
                        className="w-full rounded-sm bg-primary/80"
                        style={{ height: `${Math.max(12, (Number(row.numeric_value ?? 0) / maxValue) * 100)}%` }}
                      />
                    ))}
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                    <div className="rounded-md border border-border bg-background/60 px-2 py-1">
                      <div className="text-muted-foreground">Avg</div>
                      <div className="font-medium">{values.length ? `${avgValue}${unit ? ` ${unit}` : ""}` : "n/a"}</div>
                    </div>
                    <div className="rounded-md border border-border bg-background/60 px-2 py-1">
                      <div className="text-muted-foreground">Min</div>
                      <div className="font-medium">{values.length ? `${minValue}${unit ? ` ${unit}` : ""}` : "n/a"}</div>
                    </div>
                    <div className="rounded-md border border-border bg-background/60 px-2 py-1">
                      <div className="text-muted-foreground">Max</div>
                      <div className="font-medium">{values.length ? `${maxValue}${unit ? ` ${unit}` : ""}` : "n/a"}</div>
                    </div>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">{rows.length} readings loaded</div>
                </div>
              );
            })}
            {!vitalsByType.length ? (
              <div className="rounded-md border border-border bg-background/60 p-4 text-sm text-muted-foreground">
                No vitals synced yet.
              </div>
            ) : null}
          </div>
        </div>
      );
    }

    if (activeTab === "Body") {
      return (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {bodyByType.map(([type, rows]) => {
            const latest = rows[0];
            return (
              <div key={type} className="rounded-md border border-border bg-background/60 p-4">
                <div className="text-sm text-muted-foreground">{metricLabel(type)}</div>
                <div className="mt-2 text-2xl font-semibold">{formatMetricValue(latest)}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {latest?.recorded_at ? format(new Date(String(latest.recorded_at)), "MMM d, p") : "No reading"}
                </div>
              </div>
            );
          })}
          {!bodyByType.length ? (
            <div className="rounded-md border border-border bg-background/60 p-4 text-sm text-muted-foreground md:col-span-2">
              No body metrics synced. Connect a scale or enable body measurements in Health Connect.
            </div>
          ) : null}
        </div>
      );
    }

    if (activeTab === "Data") {
      const batchRows = data.batches?.batches ?? FIXTURE_DASHBOARD.batches?.batches ?? [];
      const countsRows = Object.entries(data.status?.counts ?? FIXTURE_DASHBOARD.status?.counts ?? {});
      return (
        <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]">
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              {countsRows.map(([label, value]) => (
                <div key={label} className="rounded-md border border-border bg-background/60 px-3 py-2">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
                  <div className="mt-1 text-lg font-semibold">{formatNumber(value)}</div>
                </div>
              ))}
            </div>
            <div className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm text-muted-foreground">
              <div className="font-medium text-foreground">Status</div>
              <div className="mt-1">Last sync: {data.status?.last_sync_at ? formatDistanceToNow(new Date(data.status.last_sync_at), { addSuffix: true }) : "fixture preview"}</div>
              <div>Threshold: {config?.stale_sync_threshold_minutes ?? 180} minutes</div>
              <div>Source: {data.source === "live" ? "Live data" : "Fixture preview"}</div>
            </div>
          </div>
          <div className="space-y-3">
            <div className="panel-title">Recent batches</div>
            <div className="space-y-2">
              {batchRows.map((batch) => (
                <details key={String(batch.batch_id)} className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm">
                  <summary className="flex cursor-pointer items-center justify-between gap-3">
                    <span className="font-medium">{String(batch.received_at ?? batch.batch_id)}</span>
                    <span className="pill">{String(batch.status ?? "unknown")}</span>
                  </summary>
                  <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                    <div>Batch: {String(batch.batch_id)}</div>
                    <div>Source: {String(batch.source ?? "")}</div>
                    <div>Processed: {formatNumber(batch.processed_count)}</div>
                    <div>Errors: {formatNumber(batch.error_count)}</div>
                    <div>Notes: {String(batch.notes ?? "")}</div>
                  </div>
                  <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
                    {JSON.stringify(batch.payload_json ?? {}, null, 2)}
                  </pre>
                </details>
              ))}
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === "Reports") {
      return (
        <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]">
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm">
                <div className="text-muted-foreground">Start date</div>
                <input
                  type="date"
                  value={reportStartValue}
                  onChange={(event) => setReportStartDate(event.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2"
                />
              </label>
              <label className="space-y-1 text-sm">
                <div className="text-muted-foreground">End date</div>
                <input
                  type="date"
                  value={reportEndValue}
                  onChange={(event) => setReportEndDate(event.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2"
                />
              </label>
            </div>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={reportStream}
                onChange={(event) => setReportStream(event.target.checked)}
                disabled={!config?.llm_enabled}
              />
              Stream wording when an LLM is configured
            </label>
            <button
              type="button"
              onClick={handleReportSubmit}
              disabled={reportLoading}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              <Send className="h-4 w-4" />
              {reportLoading ? "Generating..." : "Generate report"}
            </button>
            {reportError ? <div className="text-sm text-red-400">{reportError}</div> : null}
            <div className="text-xs text-muted-foreground">
              {config?.report_disclaimer ?? "This is a non-diagnostic summary of trends from your own health data."}
            </div>
          </div>
          <div className="rounded-md border border-border bg-background/60 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm font-medium">Report output</div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void copyReportOutput()}
                  disabled={!reportOutput}
                  className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
                >
                  Copy
                </button>
                <button
                  type="button"
                  onClick={downloadReportMarkdown}
                  disabled={!reportOutput}
                  className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
                >
                  Export MD
                </button>
                <button
                  type="button"
                  onClick={() => window.print()}
                  disabled={!reportOutput}
                  className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
                >
                  Print
                </button>
              </div>
            </div>
            <pre className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
              {reportOutput || "Generated report text appears here."}
            </pre>
          </div>
        </div>
      );
    }

    if (activeTab === "Ask") {
      if (!config?.llm_enabled) {
        return (
          <div className="rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground">
            <div className="font-medium text-foreground">Ask requires an LLM</div>
            <p className="mt-2">
              The Ask feature uses a language model to answer questions about your health data.
              No LLM is configured in this deployment.
            </p>
            <p className="mt-3">To enable it, set these environment variables in your <code className="rounded bg-muted px-1 py-0.5">docker-compose.yml</code> and rebuild the API container:</p>
            <pre className="mt-3 rounded-md bg-muted p-3 text-xs">
{`HEALTHQUERY_LLM_BASE_URL=https://api.openai.com/v1
HEALTHQUERY_LLM_MODEL=gpt-4o-mini
HEALTHQUERY_LLM_API_KEY=sk-...

# Or point at a local Ollama instance:
HEALTHQUERY_LLM_BASE_URL=http://ollama:11434/v1
HEALTHQUERY_LLM_MODEL=llama3
HEALTHQUERY_LLM_API_KEY=`}
            </pre>
          </div>
        );
      }

      return (
        <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]">
          <div className="space-y-3">
            <label className="space-y-1 text-sm">
              <div className="text-muted-foreground">Question</div>
              <textarea
                value={askQuestion}
                onChange={(event) => setAskQuestion(event.target.value)}
                rows={4}
                className="w-full rounded-md border border-border bg-background px-3 py-2"
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm">
                <div className="text-muted-foreground">Start date</div>
                <input
                  type="date"
                  value={askStartValue}
                  onChange={(event) => setAskStartDate(event.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2"
                />
              </label>
              <label className="space-y-1 text-sm">
                <div className="text-muted-foreground">End date</div>
                <input
                  type="date"
                  value={askEndValue}
                  onChange={(event) => setAskEndDate(event.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-2"
                />
              </label>
            </div>
            <button
              type="button"
              onClick={handleAskSubmit}
              disabled={askLoading}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              <MessageSquare className="h-4 w-4" />
              {askLoading ? "Asking..." : "Ask"}
            </button>
            {askError ? <div className="text-sm text-red-400">{askError}</div> : null}
            <div className="text-xs text-muted-foreground">
              Ask stays read-only and summarizes the selected date range without unrestricted SQL.
            </div>
          </div>
          <div className="rounded-md border border-border bg-background/60 p-3">
            <div className="text-sm font-medium">Answer</div>
            <pre className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
              {askOutput || "Ask output appears here."}
            </pre>
          </div>
        </div>
      );
    }

    if (activeTab === "Settings") {
      async function handleSettingsSave() {
        setSettingsSaving(true);
        setSettingsError("");
        setSettingsSaved(false);
        try {
          const payload: Record<string, string> = {
            llm_base_url: settingsForm.llm_base_url,
            llm_model: settingsForm.llm_model,
          };
          if (settingsForm.llm_api_key !== "") {
            payload.llm_api_key = settingsForm.llm_api_key;
          }
          const updated = await apiPut("/health/config", payload) as HealthConfig;
          setData((prev) => ({ ...prev, config: updated }));
          setSettingsForm((prev) => ({ ...prev, llm_api_key: "" }));
          setSettingsSaved(true);
        } catch (err) {
          setSettingsError(err instanceof Error ? err.message : "Save failed");
        } finally {
          setSettingsSaving(false);
        }
      }

      return (
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-4">
            <div className="rounded-md border border-border bg-background/60 px-3 py-3 text-sm">
              <div className="font-medium">Operational defaults</div>
              <div className="mt-2 space-y-1 text-muted-foreground">
                <div>Report window: {config?.report_window_days ?? 7} days</div>
                <div>Timeline window: {config?.timeline_window_days ?? 14} days</div>
                <div>Sync threshold: {config?.stale_sync_threshold_minutes ?? 180} minutes</div>
              </div>
            </div>

            <div className="rounded-md border border-border bg-background/60 p-3 text-sm">
              <div className="font-medium">LLM configuration</div>
              <p className="mt-1 text-xs text-muted-foreground">
                Settings saved here override environment variables.
                Any OpenAI-compatible endpoint works.
              </p>
              <div className="mt-3 space-y-3">
                <label className="block space-y-1">
                  <div className="text-xs text-muted-foreground">Base URL</div>
                  <input
                    type="url"
                    placeholder="https://api.openai.com/v1"
                    value={settingsForm.llm_base_url}
                    onChange={(e) => setSettingsForm((prev) => ({ ...prev, llm_base_url: e.target.value }))}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  />
                </label>
                <label className="block space-y-1">
                  <div className="text-xs text-muted-foreground">Model</div>
                  <input
                    type="text"
                    placeholder="gpt-4o-mini"
                    value={settingsForm.llm_model}
                    onChange={(e) => setSettingsForm((prev) => ({ ...prev, llm_model: e.target.value }))}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  />
                </label>
                <label className="block space-y-1">
                  <div className="text-xs text-muted-foreground">
                    API key{config?.llm_api_key_set ? " (currently set — leave blank to keep existing)" : ""}
                  </div>
                  <input
                    type="password"
                    placeholder={config?.llm_api_key_set ? "••••••••" : "sk-... (leave blank for Ollama)"}
                    value={settingsForm.llm_api_key}
                    onChange={(e) => setSettingsForm((prev) => ({ ...prev, llm_api_key: e.target.value }))}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  />
                </label>
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => void handleSettingsSave()}
                    disabled={settingsSaving}
                    className="inline-flex items-center gap-2 rounded-md border border-border bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
                  >
                    {settingsSaving ? "Saving..." : "Save"}
                  </button>
                  {settingsSaved ? <span className="text-xs text-green-500">Saved</span> : null}
                  {settingsError ? <span className="text-xs text-red-400">{settingsError}</span> : null}
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-md border border-border bg-background/60 px-3 py-3 text-sm">
              <div className="font-medium">LLM status</div>
              <div className="mt-2 text-muted-foreground">
                {config?.llm_enabled
                  ? `Enabled${config.llm_model ? ` · ${config.llm_model}` : ""}${config.llm_base_url ? ` · ${config.llm_base_url}` : ""}`
                  : "Disabled — Ask tab hidden until configured"}
              </div>
            </div>
            <div className="rounded-md border border-border bg-background/60 p-3 text-sm text-muted-foreground">
              {config?.report_disclaimer ?? "This is a non-diagnostic summary of trends from your own health data."}
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="space-y-2 text-sm text-muted-foreground">
        <div>Selected tab: {activeTab}</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-card/60">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 lg:px-6 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">HealthQuery</div>
            <h1 className="text-2xl font-semibold tracking-tight">Personal health dashboard</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Structured health views first. Optional summaries and reports on top.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="pill">{window.location.hostname}</span>
            <span className="pill">{data.source === "live" ? "Live data" : "Fixture preview"}</span>
            <span className={`pill ${syncIsStale ? "border-warning bg-warning/15 text-warning-foreground" : ""}`}>
              Last sync {lastSyncAt ? formatDistanceToNow(new Date(lastSyncAt), { addSuffix: true }) : "fixture"}
            </span>
            {syncIsStale ? <span className="pill border-warning bg-warning/15 text-warning-foreground">Stale</span> : null}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 lg:px-6">
        <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {cards.map((card) => {
            const Icon = card.icon;
            return (
              <div key={card.label} className="panel p-3 sm:p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm text-muted-foreground">{card.label}</div>
                    <div className="metric-value mt-1">{card.value}</div>
                  </div>
                  <div className="rounded-md border border-border bg-muted p-2 text-primary">
                    <Icon className="h-4 w-4" />
                  </div>
                </div>
                <div className="mt-3 text-xs text-muted-foreground">{card.delta}</div>
              </div>
            );
          })}
        </section>

        <section className="mt-6 grid gap-6 xl:grid-cols-[280px_1fr]">
          <div className="panel self-start p-4 xl:sticky xl:top-6">
            <div className="panel-title">Health tabs</div>
            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-1">
              {tabGroups.map(([Icon, label]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => setActiveTab(label)}
                  className={`flex w-full items-center justify-between rounded-md border px-3 py-3 text-left text-sm transition ${
                    activeTab === label
                      ? "border-primary bg-muted/70"
                      : "border-border bg-background hover:border-primary/60 hover:bg-muted/60"
                  }`}
                >
                  <span>{label}</span>
                  <Icon className="h-4 w-4 text-muted-foreground" />
                </button>
              ))}
            </div>
            <div className="mt-4 rounded-md border border-border bg-background/60 px-3 py-2 text-sm text-muted-foreground">
              <div className="font-medium text-foreground">Last sync</div>
              <div className="mt-1">
                {lastSyncAt ? formatDistanceToNow(new Date(lastSyncAt), { addSuffix: true }) : "fixture preview"}
              </div>
              <div className="mt-1">Source: {data.source === "live" ? "Live data" : "Fixture preview"}</div>
            </div>
          </div>

          <div className="panel p-4">
            <div className="panel-title">{activeTab}</div>
            <div className="mt-4">{renderSelectedTabContent()}</div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
