"use client";

import { useEffect } from "react";
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

  // Navigate to page — only when there is NO highlight keyword
  // (when there IS a keyword, the highlight effect handles navigation via jumpToMatch)
  useEffect(() => {
    if (targetPage != null && targetPage >= 1 && !highlightKeyword) {
      const timer = setTimeout(() => {
        defaultLayoutPluginInstance.toolbarPluginInstance.pageNavigationPluginInstance.jumpToPage(
          targetPage - 1
        );
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [targetPage, highlightKeyword]);

  // Highlight keyword + jump to match on the correct page
  useEffect(() => {
    if (highlightKeyword) {
      let cancelled = false;
      const timer = setTimeout(async () => {
        // Build candidates from most specific to least
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
          const matches = await searchPluginInstance.highlight([
            { keyword: candidate, matchCase: false },
          ]);
          if (matches.length > 0) {
            // Jump to the match on the target page instead of the first match
            if (targetPage != null && targetPage >= 1) {
              const pageIdx = targetPage - 1; // 0-indexed
              // Find the first match on the target page
              const idx = matches.findIndex((m) => m.pageIndex === pageIdx);
              if (idx >= 0) {
                searchPluginInstance.jumpToMatch(idx);
              } else {
                // No match on target page — find nearest page match
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
            }
            break;
          }
        }
      }, 400);
      return () => {
        cancelled = true;
        clearTimeout(timer);
      };
    } else {
      searchPluginInstance.clearHighlights();
    }
  }, [highlightKeyword, targetPage]);

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
