import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import type { Segment } from "./types";
import { openCaptionWindow, prepareCaptionWindow, type CaptionWindow } from "./caption-window";
import captionWindowStyles from "./studio.css?inline";

interface Props {
  segments: Segment[];
  status: string;
  active: boolean;
  stopping: boolean;
  onStop?: () => void;
}

function CaptionText({ segments, announce }: { segments: Segment[]; announce: boolean }) {
  const stage = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = stage.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [segments]);

  const current = segments[segments.length - 1];
  return (
    <div className="live-caption-stage" ref={stage} role="log" aria-label="当前实时字幕"
      aria-live={announce ? "polite" : "off"} aria-relevant="additions text">
      <div className="live-caption-lines">
        {segments.slice(-3, -1).map((segment, index) => (
          <p className="live-caption-previous" key={`${segment.start_ms}-${index}`}>{segment.text}</p>
        ))}
        <p className={`live-caption-current${current ? "" : " live-caption-empty"}`}>
          {current?.text || "开始收音后，字幕会显示在这里"}
        </p>
      </div>
    </div>
  );
}

export default function LiveCaptions({ segments, status, active, stopping, onStop }: Props) {
  const [floating, setFloating] = useState<(CaptionWindow & { root: HTMLElement }) | null>(null);
  const [opening, setOpening] = useState(false);
  const [inlineOpen, setInlineOpen] = useState(false);
  const requested = useRef(false);
  const [error, setError] = useState<{ message: string; detail: string } | null>(null);
  const titleId = useId();
  const windowRef = useRef<CaptionWindow | null>(null);
  const mounted = useRef(true);
  const openingRef = useRef(false);
  const openButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      windowRef.current?.window.close();
      windowRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!floating) return;
    const onClose = () => {
      requested.current = false;
      windowRef.current = null;
      if (mounted.current) {
        setFloating(null);
        openButton.current?.focus();
      }
    };
    const closeWithPage = () => floating.window.close();
    floating.window.addEventListener("pagehide", onClose);
    window.addEventListener("pagehide", closeWithPage);
    return () => {
      floating.window.removeEventListener("pagehide", onClose);
      window.removeEventListener("pagehide", closeWithPage);
    };
  }, [floating]);

  const open = async () => {
    requested.current = true;
    if (windowRef.current && !windowRef.current.window.closed) {
      windowRef.current.window.focus();
      return;
    }
    if (openingRef.current) return;
    openingRef.current = true;
    // 先绘制反馈，再申请浏览器窗口；部分内置浏览器会延迟原生窗口调用。
    flushSync(() => setOpening(true));
    setError(null);
    let target: CaptionWindow | null = null;
    try {
      target = await openCaptionWindow();
      if (!mounted.current || !requested.current) { target.window.close(); return; }
      const root = prepareCaptionWindow(target.window, document, captionWindowStyles);
      windowRef.current = target;
      setFloating({ ...target, root });
      setInlineOpen(false);
    } catch (cause) {
      target?.window.close();
      if (mounted.current && requested.current) {
        setInlineOpen(true);
        setError({
        message: "当前浏览器使用页面内小窗。需要跨应用置顶时，请用 Chrome 或 Edge 打开本应用。",
        detail: cause instanceof Error ? cause.message : String(cause),
        });
      }
    } finally {
      openingRef.current = false;
      if (mounted.current) setOpening(false);
    }
  };

  const returnToPage = () => {
    requested.current = false;
    setInlineOpen(false);
    window.focus();
    floating?.window.close();
  };

  return (
    <>
      <section className="live-captions" aria-labelledby={titleId}>
        <header className="live-caption-header">
          <h2 id={titleId}>实时字幕</h2>
          <button type="button" className="caption-button" ref={openButton} disabled={opening} onClick={open}>
            {opening ? "正在打开…" : floating || inlineOpen ? "查看字幕小窗" : "悬浮显示"}
          </button>
        </header>
        <CaptionText segments={segments} announce={!floating && !inlineOpen} />
        <footer className="live-caption-footer">
          <span className={`caption-status${active ? " is-recording" : ""}`}>{status}</span>
          <span>完整内容保留在下方字幕列表</span>
        </footer>
        {error && <div className="caption-window-error">
          <p>{error.message}</p>
          <details><summary>查看详情</summary><pre>{error.detail}</pre></details>
        </div>}
      </section>
      {(floating || inlineOpen) && createPortal(
        <section className={`live-captions live-captions-detached${floating ? "" : " caption-inline-window"}`}
          aria-label="实时字幕小窗">
          <header className="live-caption-header">
            <span className={`caption-status${active ? " is-recording" : ""}`}>{status}</span>
            <div className="caption-actions">
              {!floating && <button type="button" className="caption-button" disabled={opening} onClick={open}>
                {opening ? "正在打开…" : "重试独立窗口"}
              </button>}
              {onStop && <button type="button" className="caption-button" disabled={stopping} onClick={onStop}>
                {stopping ? "正在补完…" : "停止收音"}
              </button>}
              <button type="button" className="caption-button" onClick={returnToPage}>
                {floating ? "返回主页面" : "收起字幕"}
              </button>
            </div>
          </header>
          <CaptionText segments={segments} announce />
          <footer className="live-caption-footer">
            <span>请保持主页面打开 · 关闭小窗不影响收音</span>
            {!floating && <span>页面内小窗 · 可拖动右下角调整大小</span>}
            {floating?.kind === "popup" && <span>普通窗口不会自动置顶</span>}
          </footer>
        </section>, floating?.root ?? document.body,
      )}
    </>
  );
}
