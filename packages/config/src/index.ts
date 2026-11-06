import { z } from 'zod';

export const apiEnvSchema = z.object({
  API_PORT: z.coerce.number().int().min(1).max(65535).optional(),
  API_HOST: z.string().trim().min(1).default('127.0.0.1'),
  PORT: z.coerce.number().int().min(1).max(65535).default(4000),
  DATABASE_URL: z.string().default('postgresql://user:password@localhost:5432/study_agent'),
  OPENAI_API_KEY: z.string().optional(),
  OPENAI_BASE_URL: z.string().default('https://api.openai.com/v1'),
  OPENAI_MODEL: z.string().default('gpt-4.1-mini'),
  OPENAI_TTS_MODEL: z.string().default('tts-1'),
  OPENAI_TTS_VOICE: z.string().default('alloy'),
  // Optional separate OpenAI-compatible speech endpoint; empty values fall back to OPENAI_BASE_URL / OPENAI_API_KEY.
  OPENAI_TTS_BASE_URL: z.string().trim().url().optional().or(z.literal('').transform(() => undefined)),
  OPENAI_TTS_API_KEY: z.string().trim().min(1).optional().or(z.literal('').transform(() => undefined)),
  MINIMAX_API_KEY: z.string().optional(),
  MINIMAX_TTS_BASE_URL: z.string().url().default('https://api.minimaxi.com/v1/t2a_v2'),
  MINIMAX_TTS_MODEL: z.string().default('speech-2.8-turbo'),
  MINIMAX_TTS_VOICE: z.string().default('Chinese (Mandarin)_Warm_Girl'),
  MINIMAX_TTS_SPEED: z.coerce.number().min(0.5).max(2).default(1),
  MINIMAX_TTS_VOLUME: z.coerce.number().min(0.1).max(10).default(1),
  MINIMAX_TTS_PITCH: z.coerce.number().int().min(-12).max(12).default(0),
  MINIMAX_TTS_EMOTION: z.string().optional(),
  MINIMAX_TTS_MAX_RETRIES: z.coerce.number().int().min(0).max(3).default(1),
  CODEX_COMMAND: z.string().default('codex'),
  CODEX_MODEL: z.string().optional(),
  CODEX_TIMEOUT_MS: z.coerce.number().int().positive().default(120000),
  CODEX_PDF_TIMEOUT_MS: z.coerce.number().int().positive().default(240000),
  CODEX_PDF_MODEL: z.string().trim().min(1).default('deepseek-v4-flash'),
  XIAOJIU_CHAT_ENABLED: z.enum(['true', 'false']).default('true'),
  XIAOJIU_CHAT_DEFAULT_MODEL: z.string().trim().min(1).default('deepseek-v4-flash'),
  XIAOJIU_CHAT_TIMEOUT_MS: z.coerce.number().int().positive().default(90000),
  JWT_SECRET: z.string().default('study-agent-dev-secret'),
  TEXTBOOK_BASE_PATH: z.string().default('E:\\ChinaTextbook'),
});

export type ApiEnv = z.infer<typeof apiEnvSchema>;

export const webEnvSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z.string().default('http://localhost:4000'),
  PORT: z.coerce.number().optional(),
  WEB_PORT: z.coerce.number().optional(),
});

export type WebEnv = z.infer<typeof webEnvSchema>;

export function loadApiEnv(source: Record<string, string | undefined> = process.env): ApiEnv {
  return apiEnvSchema.parse(source);
}

export function loadWebEnv(source: Record<string, string | undefined> = process.env): WebEnv {
  return webEnvSchema.parse(source);
}
