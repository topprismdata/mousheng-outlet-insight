// render_flint.mjs — Flint spec → 自包含 HTML 的渲染桥
//
// 用法:
//   node render_flint.mjs <spec.json> <out.html> [backend] [palette] [theme]
//   backend ∈ {echarts|plotly|vegalite}，默认 echarts
//   palette ∈ {auto|rank|ramp|single|categorical|none}，默认 auto
//   theme   ∈ {light|dark|none}，默认 light
//
// 设计：
//   - 用 flint-chart 把 Flint ChartAssemblyInput 编译成目标后端的原生 spec
//   - 内联对应运行时（echarts.min.js / plotly / vega+vega-lite），产出离线可打开的单文件 HTML
//   - 不依赖任何 CDN，断网也能看
//   - ★ 配色/主题层：Flint 本身只有确定性默认色（单系列柱图会平铺成一色），
//     这里补一套"数据感知"配色（领奖台高亮 / 值渐变 / 精选色板）+ 亮暗主题。

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const flint = require("flint-chart");

const [specPath, outPath, backend = "echarts", palette = "auto", theme = "light"] =
  process.argv.slice(2);
if (!specPath || !outPath) {
  console.error(
    "用法: node render_flint.mjs <spec.json> <out.html> [echarts|plotly|vegalite] [palette] [theme]"
  );
  process.exit(1);
}

const spec = JSON.parse(readFileSync(specPath, "utf-8"));

// =====================================================================
// 配色层
// =====================================================================
const PALETTES = {
  // 精选分类色板（现代、对比清晰，12 色）
  categorical: [
    "#2563eb", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#6366f1",
    "#84cc16", "#06b6d4",
  ],
  // 品牌蓝渐变（浅 → 深），用于按数值着色的顺序场景
  brandBlue: ["#dbeafe", "#bfdbfe", "#93c5fd", "#60a5fa", "#3b82f6", "#2563eb", "#1d4ed8"],
  // 领奖台：第 1/2/3 名
  podium: ["#1d4ed8", "#2563eb", "#3b82f6"],
  // 非高亮项的灰
  muted: "#cbd5e1",
};

// 十六进制插值
function lerpColor(a, b, t) {
  const ah = parseInt(a.slice(1), 16);
  const bh = parseInt(b.slice(1), 16);
  const ar = ah >> 16, ag = (ah >> 8) & 255, ab = ah & 255;
  const br = bh >> 16, bg = (bh >> 8) & 255, bb = bh & 255;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + bl).toString(16).slice(1);
}
// 多段渐变取色，t∈[0,1]
function multiStop(stops, t) {
  if (t <= 0) return stops[0];
  if (t >= 1) return stops[stops.length - 1];
  const seg = (stops.length - 1) * t;
  const i = Math.floor(seg);
  return lerpColor(stops[i], stops[i + 1], seg - i);
}

function applyColor(opt, spec, paletteArg) {
  if (paletteArg === "none") return; // 纯 Flint 裸输出，用于对比
  const enc = spec.chart_spec?.encodings || {};
  const sem = spec.semantic_types || {};
  const colorField =
    typeof enc.color === "object" ? enc.color.field
    : typeof enc.color === "string" ? enc.color
    : null;

  const series = opt.series || [];

  // —— 单系列柱/折线图：Flint 默认会平铺成一色，这里做数据感知着色 ——
  const isSingle = series.length === 1 && !colorField;
  if (isSingle && (series[0].type === "bar" || series[0].type === "line")) {
    const data = series[0].data.map((d) => (typeof d === "object" ? d.value : d));
    const max = Math.max(...data);
    const min = Math.min(...data);
    const norm = (v) => (max === min ? 1 : (v - min) / (max - min));

    // auto / rank：领奖台高亮（前 3 名品牌蓝，其余灰）—— 最适合排名场景
    // ramp：按数值做品牌蓝渐变（高=深）
    // single：统一品牌蓝
    // auto：折线单系列默认 single（统一品牌蓝线更专业）；柱单系列用 hint（rank 领奖台 / ramp 渐变）
    const hint = spec.palette_hint || spec.chart_spec?.palette_hint;
    const st = series[0].type;
    let mode;
    if (paletteArg !== "auto") mode = paletteArg;
    else if (st === "line") mode = "single";
    else mode = hint || "rank";

    if (mode === "single") {
      series[0].data = data.map((v) => ({ value: v, itemStyle: { color: "#2563eb" } }));
    } else if (mode === "ramp") {
      series[0].data = data.map((v) => ({
        value: v,
        itemStyle: { color: multiStop(PALETTES.brandBlue, norm(v)) },
      }));
    } else {
      // rank：按值降序排名
      const order = data.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]);
      const rankOf = {};
      order.forEach(([, i], r) => (rankOf[i] = r));
      series[0].data = data.map((v, i) => {
        const r = rankOf[i];
        const c = r < 3 ? PALETTES.podium[r] : PALETTES.muted;
        return { value: v, itemStyle: { color: c } };
      });
    }
    delete opt.color; // 让逐条 itemStyle 生效
    return;
  }

  // —— 热力图：用品牌蓝渐变覆盖 visualMap（按数值着色）——
  if (series.length && series[0].type === "heatmap" && opt.visualMap) {
    opt.visualMap.inRange = { color: PALETTES.brandBlue };
    return;
  }

  // —— 有多色编码或多系列：按语义挑色板 ——
  if (colorField) {
    const st = sem[colorField];
    opt.color =
      st === "Score" || st === "Percentage" ? PALETTES.brandBlue : PALETTES.categorical;
  } else {
    opt.color = PALETTES.categorical;
  }
}

// =====================================================================
// 手动构建层（Flint 干不了的图，直接从 spec.data 自建 ECharts option）
//   - Pie Chart / Donut Chart：Flint 虽能编译但 data 输出为空数组（bug），自建
//   - Combo Chart（柱+线双轴）：Flint 完全不认识，自建
//   - Area Chart：Flint 编译成无 areaStyle 的 line，这里补面积填充
// =====================================================================
const NEEDS_MANUAL = new Set(["Pie Chart", "Donut Chart", "Area Chart", "Combo Chart"]);

function _resolveMode(paletteArg, spec) {
  if (paletteArg !== "auto") return paletteArg;
  return spec.palette_hint || spec.chart_spec?.palette_hint || "categorical";
}

function buildManualEcharts(spec, paletteArg, themeArg) {
  const cs = spec.chart_spec || {};
  const enc = cs.encodings || {};
  const vals = (spec.data && spec.data.values) || [];
  const ct = cs.chartType;
  const title = cs.title || "";
  const xf = enc.x && enc.x.field;
  const yf = enc.y && enc.y.field;
  const y2f = enc.y2 && enc.y2.field;
  const pmode = _resolveMode(paletteArg, spec);

  let opt;

  if (ct === "Pie Chart" || ct === "Donut Chart") {
    const data = vals.map((r) => ({ name: String(r[xf]), value: Number(r[yf]) }));
    // 着色：single 全蓝 / ramp 按值顺序蓝 / 其余 categorical 逐扇区
    let colors;
    if (pmode === "single") {
      colors = data.map(() => "#2563eb");
    } else if (pmode === "ramp") {
      const mx = Math.max(...data.map((d) => d.value));
      const mn = Math.min(...data.map((d) => d.value));
      colors = data.map((d) =>
        multiStop(PALETTES.brandBlue, mx === mn ? 1 : (d.value - mn) / (mx - mn)));
    } else {
      colors = PALETTES.categorical;
    }
    data.forEach((d, i) => (d.itemStyle = { color: colors[i % colors.length] }));
    opt = {
      title: { text: title, left: "center" },
      tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
      legend: { bottom: 0, type: "scroll" },
      series: [{
        type: "pie",
        radius: ct === "Donut Chart" ? ["42%", "70%"] : "68%",
        center: ["50%", "50%"],
        data,
        label: { show: true, formatter: "{b}\n{d}%" },
        labelLine: { show: true },
        itemStyle: { borderColor: "#fff", borderWidth: 2 },
      }],
    };
  } else if (ct === "Area Chart") {
    const cat = [...new Set(vals.map((r) => String(r[xf])))];
    const seriesData = cat.map((c) => {
      const row = vals.find((r) => String(r[xf]) === c);
      const v = row ? row[yf] : null;
      return v == null ? null : Number(v);
    });
    // 面积填充：ramp 用品牌蓝线性渐变，single 用半透明蓝，其余用分类色[0]
    let lineColor = "#2563eb", fillColor;
    if (pmode === "ramp") {
      fillColor = { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: "#3b82f6" }, { offset: 1, color: "#dbeafe" }] };
      lineColor = "#2563eb";
    } else if (pmode === "single") {
      fillColor = "rgba(37,99,235,0.25)";
      lineColor = "#2563eb";
    } else {
      fillColor = PALETTES.categorical[0] + "55";
      lineColor = PALETTES.categorical[0];
    }
    opt = {
      title: { text: title, left: "center" },
      tooltip: { trigger: "axis" },
      legend: { bottom: 0, data: [yf] },
      xAxis: { type: "category", data: cat, boundaryGap: false },
      yAxis: { type: "value" },
      series: [{
        type: "line", name: yf, smooth: true, symbol: "circle", symbolSize: 6,
        data: seriesData, itemStyle: { color: lineColor }, lineStyle: { color: lineColor, width: 2 },
        areaStyle: { color: fillColor },
      }],
    };
  } else if (ct === "Combo Chart") {
    const cat = [...new Set(vals.map((r) => String(r[xf])))];
    const barData = cat.map((c) => {
      const row = vals.find((r) => String(r[xf]) === c);
      const v = row ? row[yf] : null; return v == null ? null : Number(v);
    });
    const lineData = cat.map((c) => {
      const row = vals.find((r) => String(r[xf]) === c);
      const v = row ? row[y2f] : null; return v == null ? null : Number(v);
    });
    // 组合图固定：柱品牌蓝（左轴）+ 线橙色（右轴），对比清晰
    opt = {
      title: { text: title, left: "center" },
      tooltip: { trigger: "axis" },
      legend: { bottom: 0, data: [yf, y2f] },
      xAxis: { type: "category", data: cat },
      yAxis: [
        { type: "value", name: yf },
        { type: "value", name: y2f, splitLine: { show: false } },
      ],
      series: [
        { type: "bar", name: yf, data: barData, yAxisIndex: 0, itemStyle: { color: "#2563eb" } },
        { type: "line", name: y2f, data: lineData, yAxisIndex: 1, smooth: true,
          symbol: "circle", symbolSize: 7, itemStyle: { color: "#f59e0b" },
          lineStyle: { color: "#f59e0b", width: 2 } },
      ],
    };
  } else {
    // 兜底：理论上不会走到（调用方已判断 NEEDS_MANUAL）
    opt = { title: { text: title }, series: [] };
  }

  applyTheme(opt, themeArg); // 手动图也过主题层（明暗背景/坐标轴/图例/标题）
  return opt;
}

// =====================================================================
// 主题层
// =====================================================================
function applyTheme(opt, themeArg) {
  if (themeArg === "none") return;
  const T =
    themeArg === "dark"
      ? { bg: "#0f1419", text: "#e6edf3", axis: "#8b949e", split: "rgba(255,255,255,0.08)", title: "#e6edf3" }
      : { bg: "#ffffff", text: "#1f2329", axis: "#4e5969", split: "#f0f0f0", title: "#1f2329" };

  opt.backgroundColor = T.bg;
  opt.textStyle = { ...(opt.textStyle || {}), color: T.text };

  const asArr = (v) => (Array.isArray(v) ? v : v ? [v] : []);
  const axes = [...asArr(opt.xAxis), ...asArr(opt.yAxis)];
  axes.forEach((a) => {
    if (a.axisLine) a.axisLine.lineStyle = { ...a.axisLine.lineStyle, color: T.axis };
    if (a.axisLabel) a.axisLabel.color = T.axis;
    if (a.nameTextStyle) a.nameTextStyle.color = T.title;
    if (a.splitLine) a.splitLine.lineStyle = { ...a.splitLine.lineStyle, color: T.split };
  });
  if (opt.legend) opt.legend.textStyle = { ...(opt.legend.textStyle || {}), color: T.text };
  if (opt.title) opt.title.textStyle = { ...(opt.title.textStyle || {}), color: T.title };
}

// ---- 1. 调 Flint 编译成后端原生 spec ----
let chartOption, runtime, mountScript;

const ct = spec.chart_spec?.chartType;

if (backend === "echarts") {
  if (NEEDS_MANUAL.has(ct)) {
    // Flint 干不了的图（饼/环/面积/组合）→ 从 spec.data 自建 ECharts option
    // （已在 buildManualEcharts 内完成着色 + 主题，无需再走 applyColor）
    chartOption = buildManualEcharts(spec, palette, theme);
  } else {
    chartOption = flint.assembleECharts(spec);
  }
  const ecPath = require.resolve("echarts/dist/echarts.min.js");
  runtime = readFileSync(ecPath, "utf-8");
} else if (backend === "plotly") {
  chartOption = flint.assemblePlotly(spec);
  const plPath = require.resolve("plotly.js-dist-min/plotly.min.js");
  runtime = readFileSync(plPath, "utf-8");
} else if (backend === "vegalite") {
  chartOption = flint.assembleVegaLite(spec);
  const vegaPath = require.resolve("vega/build/vega.min.js");
  const vlPath = require.resolve("vega-lite/build/vega-lite.min.js");
  const vegaEmbedPath = require.resolve("vega-embed/build/vega-embed.min.js");
  runtime =
    readFileSync(vegaPath, "utf-8") + "\n" +
    readFileSync(vlPath, "utf-8") + "\n" +
    readFileSync(vegaEmbedPath, "utf-8");
} else {
  console.error("未知 backend:", backend);
  process.exit(1);
}

// ---- 2. 配色 + 主题层（仅 echarts 做精细后处理；其余后端保持 Flint 原样）----
//     必须在构造 mountScript 之前执行，因为 mountScript 是对 chartOption 的字符串快照
//     注意：NEEDS_MANUAL 图已在 buildManualEcharts 内完成着色+主题，这里跳过 applyColor
if (backend === "echarts" && !NEEDS_MANUAL.has(ct)) {
  applyColor(chartOption, spec, palette);
  applyTheme(chartOption, theme);
}

// ---- 3. 构造挂载脚本（着色/主题已生效后再序列化）----
if (backend === "echarts") {
  mountScript = `echarts.init(document.getElementById('chart')).setOption(${JSON.stringify(chartOption)});`;
} else if (backend === "plotly") {
  mountScript = `Plotly.newPlot('chart', ${JSON.stringify(chartOption).replace(/<\/script>/g, "<\\/script>")});`;
} else {
  mountScript = `vegaEmbed('#chart', ${JSON.stringify(chartOption)});`;
}

const title = spec.chart_spec?.title || "Flint Chart";
const w = spec.chart_spec?.baseSize?.width || 720;
const h = spec.chart_spec?.baseSize?.height || 420;

// 主题对应的页面外观
const PAGE =
  theme === "dark"
    ? { bg: "#0f1419", text: "#e6edf3", card: "#161b22" }
    : { bg: "#ffffff", text: "#1f2329", card: "#ffffff" };

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>${title}</title>
<style>
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:24px;background:${PAGE.bg};color:${PAGE.text}}
  h2{font-size:18px;margin:0 0 12px}
  #chart{width:${w}px;height:${h}px}
</style>
</head>
<body>
<h2>${title}</h2>
<div id="chart"></div>
<script>${runtime}</script>
<script>${mountScript}</script>
</body>
</html>`;

writeFileSync(outPath, html, "utf-8");
console.log(
  `OK ${backend} palette=${palette} theme=${theme} -> ${outPath} (${(html.length / 1024).toFixed(0)} KB)`
);
