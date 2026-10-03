import { theme, type ThemeConfig } from "antd";

// 「录音棚 Studio」主题：暖炭灰底 + 磁带黄 accent（spec UI 重构）。
// 单一强调色（amber）保持克制；破坏性操作用 danger 红。等宽字体只用在
// 字牌/时间码/标签，正文走系统 CJK 无衬线。
export const STUDIO = {
  bg: "#141210", // 暖近黑
  bgElevated: "#1C1915",
  container: "#201C16",
  border: "#3A342B",
  text: "#ECE4D5",
  textDim: "#A89C86",
  amber: "#E8A33D",
  amberSoft: "#F0BE6E",
  rec: "#E5544B", // REC 红（danger）
  ok: "#7FB069",
};

const CJK_SANS =
  '-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC","Hiragino Sans GB",system-ui,sans-serif';
export const MONO = '"Space Mono","SFMono-Regular",Menlo,Consolas,monospace';

export const studioTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: STUDIO.amber,
    colorInfo: STUDIO.amber,
    colorError: STUDIO.rec,
    colorSuccess: STUDIO.ok,
    colorBgBase: STUDIO.bg,
    colorBgContainer: STUDIO.container,
    colorBgElevated: STUDIO.bgElevated,
    colorBorder: STUDIO.border,
    colorBorderSecondary: "#2A251E",
    colorText: STUDIO.text,
    colorTextSecondary: STUDIO.textDim,
    // Typography 的 type="secondary" 走 colorTextDescription（= colorTextTertiary），
    // 只写 colorTextSecondary 会漏：屏上仍是 antd 派生的冷白 45%（对比 4.44:1）。
    colorTextTertiary: STUDIO.textDim,
    colorTextDescription: STUDIO.textDim,
    colorTextPlaceholder: "#948976",
    // 键盘焦点环：默认 controlOutline 是主色 24%，在暖炭底上只有 1.7:1。
    controlOutline: "rgba(232, 163, 61, 0.55)",
    controlOutlineWidth: 2,
    fontFamily: CJK_SANS,
    fontFamilyCode: MONO,
    borderRadius: 10,
    borderRadiusLG: 14,
    wireframe: false,
    controlHeight: 38,
    boxShadow: "0 8px 28px -10px rgba(0,0,0,.7)",
  },
  components: {
    // solid 主键的白字压在琥珀上只有 2.86:1；深色前景与 Segmented 选中项同一套语言。
    Button: {
      fontWeight: 600,
      primaryShadow: "none",
      defaultShadow: "none",
      primaryColor: "#1A1610",
    },
    Card: { paddingLG: 22 },
    Segmented: {
      itemSelectedBg: STUDIO.amber,
      itemSelectedColor: "#1A1610",
      trackBg: "#181410",
    },
    Progress: { defaultColor: STUDIO.amber },
    // 拖放区边框走 token，别用内联 style：内联 borderColor 会压过 antd 的 :hover 规则。
    Upload: { colorBorder: STUDIO.border, colorPrimaryHover: STUDIO.amber },
    Steps: { colorPrimary: STUDIO.amber, colorTextLightSolid: "#1A1610" },
    Select: { optionSelectedBg: "rgba(232,163,61,.16)" },
    Input: { activeShadow: "0 0 0 2px rgba(232,163,61,.18)" },
  },
};
