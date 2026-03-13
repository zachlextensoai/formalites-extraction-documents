"use client";

import { useEffect, useRef } from "react";
import { Viewer, Worker, SpecialZoomLevel } from "@react-pdf-viewer/core";
import { defaultLayoutPlugin } from "@react-pdf-viewer/default-layout";

import "@react-pdf-viewer/core/lib/styles/index.css";
import "@react-pdf-viewer/default-layout/lib/styles/index.css";

interface PdfViewerProps {
  fileUrl: string;
  targetPage?: number | null;
  highlightKeyword?: string | null;
}

export default function PdfViewer({
  fileUrl,
  targetPage,
  highlightKeyword,
}: PdfViewerProps) {
  const defaultLayoutPluginInstance = defaultLayoutPlugin();
  const { searchPluginInstance } =
    defaultLayoutPluginInstance.toolbarPluginInstance;

  // Track a combined "navigation request" counter to trigger effects
  const navRequestRef = useRef(0);

  // Always navigate to the page first, then attempt highlight
  useEffect(() => {
    if (targetPage == null || targetPage < 1) return;

    navRequestRef.current += 1;
    const currentNav = navRequestRef.current;
    let cancelled = false;

    // Step 1: Navigate to the page immediately
    const navTimer = setTimeout(() => {
      if (cancelled) return;
      defaultLayoutPluginInstance.toolbarPluginInstance.pageNavigationPluginInstance.jumpToPage(
        targetPage - 1
      );
    }, 200);

    // Step 2: Attempt highlight if we have a keyword (text layer may or may not exist)
    let highlightTimer: ReturnType<typeof setTimeout> | null = null;
    if (highlightKeyword) {
      highlightTimer = setTimeout(async () => {
        if (cancelled || navRequestRef.current !== currentNav) return;

        // Build candidates from most specific to least specific
        const candidates: string[] = [];
        const trimmed = highlightKeyword.trim();
        candidates.push(trimmed);

        const words = trimmed.split(/\s+/);
        if (words.length > 4) {
          candidates.push(words.slice(0, 4).join(" "));
        }
        if (words.length > 2) {
          candidates.push(words.slice(0, 2).join(" "));
        }
        if (words.length > 1) {
          candidates.push(words[0]);
        }

        for (const candidate of candidates) {
          if (cancelled) return;
          try {
            const matches = await searchPluginInstance.highlight([
              { keyword: candidate, matchCase: false },
            ]);
            if (matches.length > 0) {
              // Jump to the match closest to our target page
              const pageIdx = targetPage - 1;
              const idx = matches.findIndex((m) => m.pageIndex === pageIdx);
              if (idx >= 0) {
                searchPluginInstance.jumpToMatch(idx);
              } else {
                // Find nearest page match
                let closestIdx = 0;
                let minDist = Infinity;
                matches.forEach((m, i) => {
                  const dist = Math.abs(m.pageIndex - pageIdx);
                  if (dist < minDist) {
                    minDist = dist;
                    closestIdx = i;
                  }
                });
                searchPluginInstance.jumpToMatch(closestIdx);
              }
              break;
            }
          } catch {
            // highlight not available (e.g. no text layer) — page nav already handled it
          }
        }
      }, 600);
    } else {
      // No keyword — clear any previous highlights
      try {
        searchPluginInstance.clearHighlights();
      } catch {
        // ignore
      }
    }

    return () => {
      cancelled = true;
      clearTimeout(navTimer);
      if (highlightTimer) clearTimeout(highlightTimer);
    };
  }, [targetPage, highlightKeyword]);

  return (
    <div className="h-full w-full">
      <Worker workerUrl="https://unpkg.com/pdfjs-dist@3.11.174/build/pdf.worker.min.js">
        <Viewer
          fileUrl={fileUrl}
          plugins={[defaultLayoutPluginInstance]}
          defaultScale={SpecialZoomLevel.PageWidth}
        />
      </Worker>
    </div>
  );
}
