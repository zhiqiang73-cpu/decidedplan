export const ENV = {
  isProduction: process.env.NODE_ENV === "production",
  // Optional: LLM integration (used by llmAnalysis endpoints only)
  forgeApiUrl: process.env.BUILT_IN_FORGE_API_URL ?? "",
  forgeApiKey: process.env.BUILT_IN_FORGE_API_KEY ?? "",
};
