import { describe, expect, it } from 'vitest';

import { PROVIDER_CATALOG } from './provider-catalog';

describe('PROVIDER_CATALOG', () => {
  it('has a healthy number of providers', () => {
    expect(PROVIDER_CATALOG.length).toBeGreaterThanOrEqual(40);
  });
  it('every provider is well-formed with unique ids', () => {
    const ids = new Set<string>();
    for (const p of PROVIDER_CATALOG) {
      expect(p.id).toMatch(/^[a-z0-9-]+$/);
      expect(ids.has(p.id)).toBe(false);
      ids.add(p.id);
      expect(p.name.length).toBeGreaterThan(0);
      expect(['openai', 'ollama', 'openai_compatible', 'litellm', 'tei']).toContain(p.provider_kind);
      expect(p.capabilities.length).toBeGreaterThan(0);
      for (const m of p.models) {
        expect(['chat', 'embedding']).toContain(m.modality);
        expect(m.litellm_model_name.length).toBeGreaterThan(0);
      }
    }
  });
  it('maps the well-known providers correctly', () => {
    const by = (id: string) => PROVIDER_CATALOG.find((p) => p.id === id);
    expect(by('openai')?.provider_kind).toBe('openai');
    expect(by('anthropic')?.provider_kind).toBe('litellm');
    expect(by('anthropic')?.models.some((m) => m.litellm_model_name.startsWith('anthropic/'))).toBe(true);
    expect(by('ollama')?.needs_base_url).toBe(true);
  });
  it('includes the supplemental inference-engine providers as litellm passthroughs', () => {
    const by = (id: string) => PROVIDER_CATALOG.find((p) => p.id === id);
    const supplements = ['baseten', 'fireworks-ai', 'cerebras', 'sambanova', 'hyperbolic', 'lambda', 'nebius'];
    for (const id of supplements) {
      const p = by(id);
      expect(p, `provider ${id} present`).toBeDefined();
      expect(p?.provider_kind).toBe('litellm');
      expect(p?.needs_api_key).toBe(true);
      expect(p?.models.length).toBeGreaterThan(0);
      // supplement models never opt into reasoning by default
      expect(p?.models.every((m) => !m.supports_reasoning)).toBe(true);
    }
  });
  it('includes the explicitly-required Baseten DeepSeek V4 Flash entry', () => {
    const baseten = PROVIDER_CATALOG.find((p) => p.id === 'baseten');
    const flash = baseten?.models.find(
      (m) => m.litellm_model_name === 'baseten/deepseek-ai/DeepSeek-V4-Flash-0731',
    );
    expect(flash).toBeDefined();
    expect(flash?.display_name).toBe('deepseek-ai/DeepSeek-V4-Flash-0731');
    expect(flash?.supports_reasoning).toBeFalsy();
  });
});
