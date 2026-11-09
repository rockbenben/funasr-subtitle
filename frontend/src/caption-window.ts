interface CaptionHost extends Window {
  documentPictureInPicture?: {
    requestWindow: (options: { width: number; height: number }) => Promise<Window>;
  };
}

export interface CaptionWindow {
  window: Window;
  kind: "pip" | "popup";
}

/** 在点击事件内申请窗口，避免浏览器将其当成自动弹窗拦截。 */
export async function openCaptionWindow(host: CaptionHost = window): Promise<CaptionWindow> {
  if (host.documentPictureInPicture) {
    try {
      let expired = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const request = host.documentPictureInPicture.requestWindow({ width: 720, height: 360 });
      void request.then((late) => { if (expired) late.close(); }, () => {});
      let target: Window;
      try {
        target = await Promise.race([request, new Promise<never>((_, reject) => {
          timer = setTimeout(() => { expired = true; reject(new Error("画中画未响应")); }, 1500);
        })]);
      } finally {
        clearTimeout(timer);
      }
      return { window: target, kind: "pip" };
    } catch {
      // 浏览器可能公开了 API 却禁用了画中画；继续尝试普通窗口。
    }
  }
  const target = host.open("", "_blank", "popup,width=720,height=360");
  if (!target) throw new Error("字幕窗口被拦截了，请允许此页面打开弹窗后重试。");
  if (target === host || target.closed) throw new Error("浏览器没有提供独立窗口，已保留主页面。");
  return { window: target, kind: "popup" };
}

export function prepareCaptionWindow(target: Window, source: Document, css: string): HTMLElement {
  const doc = target.document;
  doc.title = "实时字幕 · funasr-subtitle";
  doc.documentElement.lang = source.documentElement.lang || "zh-CN";
  const base = doc.createElement("base");
  base.href = source.baseURI;
  doc.head.append(base);
  // 独立窗口可能无法加载复制的 stylesheet 链接；内联构建后的 CSS，首帧即可正确显示。
  const style = doc.createElement("style");
  style.textContent = css;
  doc.head.append(style);
  doc.body.className = "caption-window";
  const root = doc.createElement("div");
  root.id = "caption-root";
  doc.body.replaceChildren(root);
  return root;
}
