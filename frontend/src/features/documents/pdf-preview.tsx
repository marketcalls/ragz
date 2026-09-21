import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentLoadingTask, RenderTask } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

import { Spinner } from '@/components/ui/spinner';

export function PdfPreview({ objectUrl, filename, page }: {
  objectUrl: string;
  filename: string;
  page: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [renderedPage, setRenderedPage] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let disposed = false;
    setRenderedPage(null);
    setFailed(false);
    let loadingTask: PDFDocumentLoadingTask | null = null;
    let renderTask: RenderTask | null = null;
    void (async () => {
      try {
        const { GlobalWorkerOptions, getDocument } = await import('pdfjs-dist');
        if (disposed) return;
        GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        loadingTask = getDocument({ url: objectUrl, enableXfa: false });
        const pdf = await loadingTask.promise;
        if (disposed) return;
        const targetPage = Math.max(1, Math.min(Math.trunc(page), pdf.numPages));
        const pdfPage = await pdf.getPage(targetPage);
        if (disposed) return;
        const canvas = canvasRef.current;
        if (!canvas) return;
        const viewport = pdfPage.getViewport({ scale: 1.5 });
        const outputScale = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        renderTask = pdfPage.render({
          canvas,
          viewport,
          transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
        });
        await renderTask.promise;
        if (!disposed) setRenderedPage(targetPage);
      } catch (error: unknown) {
        if (!disposed && !(error instanceof Error && error.name === 'RenderingCancelledException')) {
          setFailed(true);
        }
      }
    })();
    return () => {
      disposed = true;
      renderTask?.cancel();
      void loadingTask?.destroy();
    };
  }, [filename, objectUrl, page]);

  if (failed) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <p role="alert" className="text-[13px] text-secondary">
          This PDF could not be rendered safely. You can still download the original file.
        </p>
      </div>
    );
  }
  return (
    <div className="relative flex min-h-full justify-center overflow-auto bg-subtle p-4">
      {renderedPage === null ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <Spinner label="Rendering PDF…" />
        </div>
      ) : null}
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`Page ${renderedPage ?? page} of ${filename}`}
        data-pdf-page={renderedPage ?? undefined}
        className="max-w-full self-start bg-white shadow-sm"
      />
    </div>
  );
}
