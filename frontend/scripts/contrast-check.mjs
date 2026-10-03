// 配色守护：屏上文字与色板的对比度必须达 WCAG AA，改色板时这里会红。
// 用法：npm run check:contrast （Node 直接跑 .ts，无需构建）
//
// 说明：按钮/步骤条的「底色」是 antd 深色算法从 colorPrimary 派生后**实测渲染**的值，
// 不是 CSS 变量名——所以这里钉的是像素事实。若你改了 colorPrimary，请重跑
// design-preview 的取色探针（probe.mjs）核对这两个常量，再回来更新。
import { readFileSync } from "node:fs";
import { STUDIO, studioTheme } from "../src/theme.ts";

const SOLID_AMBER_BG = "#C98D36";   // solid 主键实测底色（由 colorPrimary 派生）
const STEPS_FINISH_BG = "#E7A23C";  // Steps 已完成序号圆底实测值

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lum = (rgb) => {
  const [r, g, b] = rgb.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const over = (fg, a, bg) => fg.map((v, i) => v * a + bg[i] * (1 - a));
const ratio = (f, b) => {
  const L1 = lum(f), L2 = lum(b);
  return Math.round(((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)) * 100) / 100;
};
const parseCss = (c) => {
  const m = /rgba?\(([^)]+)\)/.exec(c);
  if (m) {
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(parseFloat);
    return { rgb: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 };
  }
  if (c.startsWith("#")) return { rgb: hex(c), a: 1 };
  throw new Error("读不出颜色：" + c);
};

const token = studioTheme.token ?? {};
const comp = studioTheme.components ?? {};
const container = hex(token.colorBgContainer ?? STUDIO.container);
const page = hex(token.colorBgBase ?? STUDIO.bg);

const CASES = [
  ["正文 on 卡片", token.colorText, container, 4.5],
  ["次要文字 on 卡片（全站小标签）", token.colorTextSecondary, container, 4.5],
  ["占位符 on 卡片", token.colorTextPlaceholder, container, 4.5],
  ["主键文字 on solid 琥珀底", comp.Button?.primaryColor, hex(SOLID_AMBER_BG), 4.5],
  ["步骤序号 on 完成圆底", comp.Steps?.colorTextLightSolid, hex(STEPS_FINISH_BG), 4.5],
  ["分段选中文字 on 琥珀底", comp.Segmented?.itemSelectedColor, hex(STUDIO.amber), 4.5],
  ["时间码 on 卡片", STUDIO.amberSoft, container, 4.5],
  ["破坏性文字钮 on 页底（暖光晕实测底）", "#EE6B62", hex("#27211A"), 4.5],
  ["filled 主钮前景 on 实测 filled 底", STUDIO.amberSoft, hex("#543F21"), 4.5],
];

const css = readFileSync(new URL("../src/studio.css", import.meta.url), "utf8");

const fails = [];
const lines = [];

// filled 主钮与正文链接的提亮写在 CSS 里（antd 派生的暗琥珀压在暗底上只有 3.5:1）。
for (const [name, re] of [
  ["filled 主钮提亮规则", /\.ant-btn-color-primary\.ant-btn-variant-filled\s*\{\s*color:\s*var\(--amber-soft\)/],
  ["正文链接提亮规则", /\.ant-typography a\s*\{\s*color:\s*var\(--amber-soft\)/],
]) {
  if (!re.test(css)) fails.push(`studio.css 里找不到「${name}」，那处文字会退回 3.5:1 的派生暗琥珀`);
  else lines.push(`  ok     —      ${name}在`);
}

for (const [name, fgRaw, bg, need] of CASES) {
  if (!fgRaw) { fails.push(`${name}: 主题里没有这个色（token 漏了）`); continue; }
  const fg = parseCss(String(fgRaw));
  const composited = over(fg.rgb, fg.a, bg);
  const r = ratio(composited, bg);
  lines.push(`  ${r >= need ? "ok  " : "FAIL"} ${String(r).padStart(6)}:1 (≥${need})  ${name}`);
  if (r < need) fails.push(`${name} 只有 ${r}:1，要求 ≥${need}:1`);
}

// 焦点指示对比（WCAG 2.4.11 要求 ≥3:1）。
// 注意：antd 的 controlOutline 是 alias（由 colorPrimaryBg 混出来），用户 token 改不动，
// 所以焦点环写在 studio.css 里——这里同时钉「配色够亮」和「CSS 规则还在」。
{
  const css = readFileSync(new URL("../src/studio.css", import.meta.url), "utf8");
  if (!/\.ant-btn:focus-visible[\s\S]{0,240}outline:\s*2px solid var\(--amber\)(\s*!important)?/.test(css)) {
    fails.push("studio.css 里找不到 :focus-visible 的琥珀描边规则（焦点环会退回 1.7:1 的暗棕光晕）");
  } else {
    const r = ratio(hex(STUDIO.amber), container);
    lines.push(`  ${r >= 3 ? "ok  " : "FAIL"} ${String(r).padStart(6)}:1 (≥3)    键盘焦点环 on 卡片`);
    if (r < 3) fails.push(`焦点环只有 ${r}:1，键盘用户看不见（要求 ≥3:1）`);
  }
}

// 结构判据：Typography 的 type="secondary" 取的是 colorTextDescription，
// 只写 colorTextSecondary 会静默失效（曾经全站小字因此变冷白 45%）。
if (token.colorTextDescription !== token.colorTextSecondary) {
  fails.push("colorTextDescription 与 colorTextSecondary 不一致：Text type=\"secondary\" 会走前者");
} else {
  lines.push("  ok     —      Text type=\"secondary\" 的 token 已接上");
}

console.log(lines.join("\n"));
if (fails.length) {
  console.error("\n配色守护失败：\n" + fails.map((f) => "  - " + f).join("\n"));
  process.exit(1);
}
console.log("\n配色守护通过：" + (CASES.length + 2) + " 项");
