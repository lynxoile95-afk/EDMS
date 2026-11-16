import rawProviders from "../../../shared/ai-providers.json";

export interface AIProviderExtraField {
  id: string;
  label: string;
  required?: boolean;
  default?: string;
  placeholder?: string;
}

export interface AIProviderDefinition {
  id: string;
  name: string;
  icon: string;
  avatar: string;
  iconColor: string;
  defaultBaseUrl: string;
  apiType: string;
  suggestedModels: string[];
  defaultModel: string;
  keyRequired: boolean;
  baseUrlEditable: boolean;
  isLocal: boolean;
  visionCapableModels: string[];
  supportsModelList: boolean;
  extraFields?: AIProviderExtraField[];
  testEndpoint: { kind: string; path: string };
}

export const AI_PROVIDERS = rawProviders as AIProviderDefinition[];

export function providerById(providerId: string): AIProviderDefinition {
  return AI_PROVIDERS.find((provider) => provider.id === providerId) ?? AI_PROVIDERS[0];
}

export function providerHasVisionModel(provider: AIProviderDefinition, model: string): boolean {
  if (provider.visionCapableModels.length === 0) return false;
  const normalized = model.trim().toLowerCase();
  return provider.visionCapableModels.some((candidate) => {
    const value = candidate.toLowerCase();
    return normalized === value || normalized.includes(value) || value.includes(normalized);
  });
}
