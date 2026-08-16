import { useEffect, useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';

import {
  useProviderSettings,
  useUpdateProviderSettings,
  type ChunkMethod,
  type CohereRerankModel,
  type GenerativeUiImages,
  type WebSearchProvider,
} from './queries';

export function SettingsPage() {
  const settings = useProviderSettings();
  const update = useUpdateProviderSettings();

  const [parser, setParser] = useState<'anydoc' | 'docling' | 'llamaparse' | 'liteparse'>(
    'liteparse',
  );
  const [rerank, setRerank] = useState<'local' | 'cohere'>('local');
  const [cohereModel, setCohereModel] = useState<CohereRerankModel>('rerank-v4.0-fast');
  const [webSearch, setWebSearch] = useState<WebSearchProvider>('duckduckgo');
  const [fullContent, setFullContent] = useState(true);
  const [generativeUiImages, setGenerativeUiImages] = useState<GenerativeUiImages>('off');
  const [generativeUiEnabled, setGenerativeUiEnabled] = useState(true);
  const [defaultChunk, setDefaultChunk] = useState<ChunkMethod>('heading');
  // API keys are write-only: never populated from the query response, always
  // start blank, and are cleared again after every save attempt.
  const [llamaKey, setLlamaKey] = useState('');
  const [cohereKey, setCohereKey] = useState('');
  const [tavilyKey, setTavilyKey] = useState('');

  useEffect(() => {
    if (settings.data) {
      setParser(settings.data.document_parser);
      setRerank(settings.data.rerank_provider);
      setCohereModel(settings.data.cohere_rerank_model);
      setWebSearch(settings.data.web_search_provider);
      setFullContent(settings.data.web_search_full_content);
      setGenerativeUiImages(settings.data.generative_ui_images);
      setGenerativeUiEnabled(settings.data.generative_ui_enabled);
      setDefaultChunk(settings.data.default_chunk_method);
    }
  }, [settings.data]);

  // Keys are only cleared once the PUT actually succeeds — a failed save
  // (e.g. wrong key) keeps the just-typed value in the field instead of
  // silently discarding it, per review feedback.
  useEffect(() => {
    if (update.isSuccess) {
      setLlamaKey('');
      setCohereKey('');
      setTavilyKey('');
    }
  }, [update.isSuccess]);

  function onSave() {
    update.mutate({
      document_parser: parser,
      rerank_provider: rerank,
      web_search_provider: webSearch,
      web_search_full_content: fullContent,
      generative_ui_images: generativeUiImages,
      generative_ui_enabled: generativeUiEnabled,
      default_chunk_method: defaultChunk,
      // Only sent when Cohere is the selected reranker — matches the spec
      // intent (cohere_rerank_model is a Cohere-only knob) and avoids
      // clobbering a stored choice while previewing the local-reranker path.
      ...(rerank === 'cohere' ? { cohere_rerank_model: cohereModel } : {}),
      ...(llamaKey ? { llamaparse_api_key: llamaKey } : {}),
      ...(cohereKey ? { cohere_api_key: cohereKey } : {}),
      ...(tavilyKey ? { tavily_api_key: tavilyKey } : {}),
    });
  }

  return (
    <>
      <TopBar title="Settings" />
      <div className="flex-1 overflow-y-auto p-6">
        {settings.isPending ? <Spinner label="Loading settings…" /> : null}
        {settings.isError ? (
          <QueryError error={settings.error} onRetry={() => settings.refetch()} />
        ) : null}
        {settings.data ? (
          <div className="mx-auto max-w-xl space-y-8">
            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Document parser</h3>
              <div>
                <Label htmlFor="parser">Document parser</Label>
                <NativeSelect
                  id="parser"
                  value={parser}
                  onChange={(e) =>
                    setParser(e.target.value as 'anydoc' | 'docling' | 'llamaparse' | 'liteparse')
                  }
                >
                  <option value="liteparse">
                    liteparse (recommended — local, page-accurate citations, ~40× faster than
                    Docling)
                  </option>
                  <option value="anydoc">
                    anydoc (fastest — office + text PDFs; scans use Docling OCR; section-level
                    citations only, no page numbers)
                  </option>
                  <option value="docling">Docling (local, page-accurate, slow)</option>
                  <option value="llamaparse">LlamaParse (cloud, PPTX + OCR)</option>
                </NativeSelect>
              </div>
              <div>
                <Label htmlFor="llamakey">
                  LlamaParse API key
                  {settings.data.llamaparse_key_set ? ' (set — leave blank to keep)' : ''}
                </Label>
                <Input
                  id="llamakey"
                  type="password"
                  autoComplete="off"
                  value={llamaKey}
                  onChange={(e) => setLlamaKey(e.target.value)}
                  placeholder={settings.data.llamaparse_key_set ? '••••••••' : 'llx-…'}
                />
              </div>
            </section>

            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Reranker</h3>
              <div>
                <Label htmlFor="rerank">Reranker</Label>
                <NativeSelect
                  id="rerank"
                  value={rerank}
                  onChange={(e) => setRerank(e.target.value as 'local' | 'cohere')}
                >
                  <option value="local">Local (self-hosted)</option>
                  <option value="cohere">Cohere Rerank API</option>
                </NativeSelect>
              </div>
              {rerank === 'cohere' ? (
                <div>
                  <Label htmlFor="coheremodel">Cohere model</Label>
                  <NativeSelect
                    id="coheremodel"
                    value={cohereModel}
                    onChange={(e) => setCohereModel(e.target.value as CohereRerankModel)}
                  >
                    <option value="rerank-v4.0-fast">Rerank v4 Fast</option>
                    <option value="rerank-v4.0-pro">Rerank v4 Pro</option>
                  </NativeSelect>
                </div>
              ) : null}
              <div>
                <Label htmlFor="coherekey">
                  Cohere API key
                  {settings.data.cohere_key_set ? ' (set — leave blank to keep)' : ''}
                </Label>
                <Input
                  id="coherekey"
                  type="password"
                  autoComplete="off"
                  value={cohereKey}
                  onChange={(e) => setCohereKey(e.target.value)}
                  placeholder={settings.data.cohere_key_set ? '••••••••' : 'ck-…'}
                />
              </div>
            </section>

            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Web search</h3>
              <div>
                <Label htmlFor="websearch">Web search provider</Label>
                <NativeSelect
                  id="websearch"
                  value={webSearch}
                  onChange={(e) => setWebSearch(e.target.value as WebSearchProvider)}
                >
                  <option value="duckduckgo">DuckDuckGo (default, keyless)</option>
                  <option value="tavily">Tavily (cloud, needs API key)</option>
                </NativeSelect>
              </div>
              {webSearch === 'tavily' ? (
                <div>
                  <Label htmlFor="tavilykey">
                    Tavily API key
                    {settings.data.tavily_key_set ? ' (set — leave blank to keep)' : ''}
                  </Label>
                  <Input
                    id="tavilykey"
                    type="password"
                    autoComplete="off"
                    value={tavilyKey}
                    onChange={(e) => setTavilyKey(e.target.value)}
                    placeholder={settings.data.tavily_key_set ? '••••••••' : 'tvly-…'}
                  />
                </div>
              ) : null}
              <div>
                <label className="flex items-center gap-2 text-sm text-secondary">
                  <input
                    type="checkbox"
                    checked={fullContent}
                    onChange={(e) => setFullContent(e.target.checked)}
                    aria-label="Fetch full page content"
                  />
                  Fetch full page content
                </label>
                <p className="mt-1 text-xs text-muted">
                  Read the top results&apos; full page text (not just search snippets) so answers
                  to list/overview questions are complete. Fetched server-side; slightly slower.
                </p>
              </div>
              <div>
                <label className="flex items-center gap-2 text-sm text-secondary">
                  <input
                    type="checkbox"
                    checked={generativeUiEnabled}
                    onChange={(e) => setGenerativeUiEnabled(e.target.checked)}
                    aria-label="Rich generative UI"
                  />
                  Rich generative UI
                </label>
                <p className="mt-1 text-xs text-muted">
                  Render answers as visual cards, tables, charts, source cards &amp; follow-ups
                  instead of plain text. Global; applies to every workspace.
                </p>
              </div>
              <div>
                <Label htmlFor="generativeuiimages">Generative UI images</Label>
                <NativeSelect
                  id="generativeuiimages"
                  value={generativeUiImages}
                  onChange={(e) => setGenerativeUiImages(e.target.value as GenerativeUiImages)}
                >
                  <option value="off">Off (no web images)</option>
                  <option value="web_results">Web search results</option>
                </NativeSelect>
                <p className="mt-1 text-xs text-muted">
                  Show real images from web-search results inside generated cards. Images are
                  proxied server-side; never loaded directly from third-party hosts.
                </p>
              </div>
            </section>

            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Default chunking strategy</h3>
              <div>
                <Label htmlFor="defaultchunk">Default chunking strategy</Label>
                <NativeSelect
                  id="defaultchunk"
                  value={defaultChunk}
                  onChange={(e) => setDefaultChunk(e.target.value as ChunkMethod)}
                >
                  <option value="heading">Heading (structure-aware)</option>
                  <option value="fixed">Fixed-size</option>
                  <option value="page">Page</option>
                  <option value="table_qa">Table Q&amp;A</option>
                </NativeSelect>
                <p className="mt-1 text-xs text-muted">
                  New workspaces start with this; change any workspace&apos;s strategy in its
                  Settings.
                </p>
              </div>
            </section>

            {update.isError ? <QueryError error={update.error} onRetry={onSave} /> : null}
            <div className="flex items-center gap-3">
              <Button variant="primary" onClick={onSave} disabled={update.isPending}>
                {update.isPending ? 'Saving…' : 'Save'}
              </Button>
              {update.isSuccess ? <span className="text-sm text-success">Saved.</span> : null}
            </div>
          </div>
        ) : null}
      </div>
    </>
  );
}
