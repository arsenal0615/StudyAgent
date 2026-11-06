import { BadRequestException, Injectable, ServiceUnavailableException } from '@nestjs/common';
import { loadApiEnv, type ApiEnv } from '@study-agent/config';
import type { StudioSpeechCapabilities, StudioSpeechVoice } from '@study-agent/contracts';
import OpenAI from 'openai';
import { classroomVoicePresets,neutralVoiceModel,neutralVoiceText,neutralVoiceVersion,readNeutralVoiceSample } from './neutral-voice-samples';

const sampleRate = 16_000;
const maxSseBytes = 8_000_000;
const miniMaxVoices: StudioSpeechVoice[] = [
  { id: 'Chinese (Mandarin)_Gentle_Senior', label: '温柔学姐' },
  { id: 'Chinese (Mandarin)_Warm_Girl', label: '温暖少女' },
  { id: 'Chinese (Mandarin)_Soft_Girl', label: '柔和少女' },
  { id: 'qiaopi_mengmei', label: '俏皮萌妹' },
  { id: 'wumei_yujie', label: '妩媚御姐' },
];
const openAiVoices: StudioSpeechVoice[] = ['alloy', 'coral', 'nova', 'sage', 'shimmer']
  .map(id => ({ id, label: id }));
const emotions = new Set(['happy', 'sad', 'angry', 'fearful', 'disgusted', 'surprised', 'calm', 'fluent', 'whisper']);

export type SpeechResult = { content: Buffer; contentType: 'audio/wav' | 'audio/mpeg'; provider: 'minimax' | 'openai' };

export function pcm16ToWav(pcm: Buffer, rate = sampleRate, channels = 1) {
  if (!pcm.length || pcm.length % 2) throw new Error('PCM16 音频为空或字节数无效');
  const header = Buffer.alloc(44);
  header.write('RIFF', 0, 'ascii'); header.writeUInt32LE(36 + pcm.length, 4); header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii'); header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22); header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * channels * 2, 28); header.writeUInt16LE(channels * 2, 32);
  header.writeUInt16LE(16, 34); header.write('data', 36, 'ascii'); header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

export function buildMiniMaxRequest(env: ApiEnv, text: string, voice?: string) {
  const emotion = env.MINIMAX_TTS_EMOTION?.trim().toLowerCase();
  return {
    model: env.MINIMAX_TTS_MODEL,
    text,
    stream: true,
    stream_options: { exclude_aggregated_audio: true },
    voice_setting: { voice_id: voice || env.MINIMAX_TTS_VOICE, speed: env.MINIMAX_TTS_SPEED,
      vol: env.MINIMAX_TTS_VOLUME, pitch: env.MINIMAX_TTS_PITCH, ...(emotion ? { emotion } : {}) },
    audio_setting: { sample_rate: sampleRate, format: 'pcm', channel: 1 },
    subtitle_enable: false,
    output_format: 'hex',
    aigc_watermark: false,
  };
}

export function decodeMiniMaxSse(input: string) {
  const chunks: Buffer[] = [];
  for (const original of input.split(/\r?\n/)) {
    let line = original.trim(); if (!line || line.startsWith(':') || line === 'data: [DONE]' || line === '[DONE]') continue;
    if (line.startsWith('data:')) line = line.slice(5).trim();
    if (!line.startsWith('{')) continue;
    let payload: Record<string, unknown>;
    try { payload = JSON.parse(line) as Record<string, unknown>; }
    catch { throw new ServiceUnavailableException('语音服务返回了无法解析的数据'); }
    const base = payload.base_resp && typeof payload.base_resp === 'object' ? payload.base_resp as Record<string, unknown> : {};
    const code = Number(base.status_code || 0);
    if (code) {
      const trace = typeof payload.trace_id === 'string' ? payload.trace_id : '';
      throw new ServiceUnavailableException(`语音服务错误 ${code}: ${String(base.status_msg || '未知错误')}${trace ? `（追踪 ${trace.slice(0, 80)}）` : ''}`);
    }
    const data = payload.data && typeof payload.data === 'object' ? payload.data as Record<string, unknown> : {};
    const audio = typeof data.audio === 'string' ? data.audio : '';
    if (!audio) continue;
    if (audio.length % 2 || !/^[0-9a-f]+$/i.test(audio)) throw new ServiceUnavailableException('语音服务返回了无效的 PCM 数据');
    chunks.push(Buffer.from(audio, 'hex'));
  }
  if (!chunks.length) throw new ServiceUnavailableException('语音服务未返回可用音频');
  return Buffer.concat(chunks);
}

@Injectable()
export class SpeechService {
  private readonly env = loadApiEnv();
  // Speech may use its own OpenAI-compatible endpoint (e.g. a local TTS service) while chat keeps OPENAI_BASE_URL.
  private readonly openai = (this.env.OPENAI_TTS_API_KEY ?? this.env.OPENAI_API_KEY) && !this.env.MINIMAX_API_KEY
    ? new OpenAI({ apiKey: this.env.OPENAI_TTS_API_KEY ?? this.env.OPENAI_API_KEY, baseURL: this.env.OPENAI_TTS_BASE_URL ?? this.env.OPENAI_BASE_URL,
      timeout: 20_000, maxRetries: 0 })
    : null;

  get provider(): 'minimax' | 'openai' | null {
    if (this.env.MINIMAX_API_KEY) return 'minimax';
    if (this.openai) return 'openai';
    return null;
  }

  capabilities(): StudioSpeechCapabilities {
    const provider = this.provider;
    const voices = provider === 'minimax' ? [...classroomVoicePresets,...miniMaxVoices.filter(row=>!classroomVoicePresets.some(preset=>preset.id===row.id))] : provider === 'openai' ? openAiVoices : [];
    return { available: Boolean(provider), provider, voices,
      ...(provider?{preview:{version:neutralVoiceVersion,text:neutralVoiceText,model:provider==='minimax'?neutralVoiceModel:null}}:{}),
      defaultVoice: provider === 'minimax' ? this.env.MINIMAX_TTS_VOICE : provider === 'openai' ? this.env.OPENAI_TTS_VOICE : null };
  }

  async preview(voice:string,version:string,signal?:AbortSignal):Promise<SpeechResult>{
    if(signal?.aborted)throw new BadRequestException('试听已停止');
    const capabilities=this.capabilities();
    if(!capabilities.available)throw new ServiceUnavailableException('当前尚未启用语音，文字课堂仍可使用');
    if(version!==neutralVoiceVersion||!capabilities.voices.some(row=>row.id===voice))throw new BadRequestException('音色或试听版本已变化，请重新读取课堂设置');
    const content=capabilities.provider==='minimax'?readNeutralVoiceSample(voice):null;
    if(content){if(signal?.aborted)throw new BadRequestException('试听已停止');return{content,contentType:'audio/mpeg',provider:'minimax'};}
    return this.synthesize(neutralVoiceText,voice,signal);
  }

  async synthesize(text: string, requestedVoice?: string, signal?:AbortSignal): Promise<SpeechResult> {
    if(signal?.aborted)throw new BadRequestException('朗读已停止');
    const clean = text.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!clean) throw new BadRequestException('旁白文本不能为空');
    if(clean.length>4096)throw new BadRequestException('单段朗读超过4096字，请拆分讲解后重试，文字内容仍可阅读');
    const capabilities = this.capabilities();
    if (!capabilities.provider) throw new ServiceUnavailableException('未配置白板语音服务');
    if (requestedVoice && !capabilities.voices.some(voice => voice.id === requestedVoice)) {
      throw new BadRequestException('不支持的旁白音色');
    }
    if (capabilities.provider === 'minimax') return { content: await this.minimax(clean, requestedVoice,signal),
      contentType: 'audio/wav', provider: 'minimax' };
    if (!this.openai) throw new ServiceUnavailableException('OpenAI 语音服务不可用');
    try {
      const speech = await this.openai.audio.speech.create({ model: this.env.OPENAI_TTS_MODEL,
        voice: requestedVoice || this.env.OPENAI_TTS_VOICE, input: clean, response_format: 'mp3' },{signal});
      return { content: Buffer.from(await speech.arrayBuffer()), contentType: 'audio/mpeg', provider: 'openai' };
    } catch { if(signal?.aborted)throw new BadRequestException('朗读已停止');throw new ServiceUnavailableException('OpenAI 旁白暂时不可用，可以稍后重试或使用字幕视频'); }
  }

  private async minimax(text: string, requestedVoice?: string, signal?:AbortSignal) {
    const base = new URL(this.env.MINIMAX_TTS_BASE_URL);
    if (base.protocol !== 'https:' || base.username || base.password) throw new ServiceUnavailableException('语音服务地址配置无效');
    const emotion = this.env.MINIMAX_TTS_EMOTION?.trim().toLowerCase();
    if (emotion && !emotions.has(emotion)) throw new ServiceUnavailableException('语音情绪配置无效');
    const body = JSON.stringify(buildMiniMaxRequest(this.env, text, requestedVoice));
    let lastError: unknown;
    for (let attempt = 0; attempt <= this.env.MINIMAX_TTS_MAX_RETRIES; attempt += 1) {
      if(signal?.aborted)throw new BadRequestException('朗读已停止');
      const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30_000);
      try {
        const response = await fetch(base, { method: 'POST', signal: signal?AbortSignal.any([signal,controller.signal]):controller.signal,
          headers: { authorization: `Bearer ${this.env.MINIMAX_API_KEY}`, 'content-type': 'application/json', accept: 'text/event-stream' }, body });
        if (!response.ok) throw new ServiceUnavailableException(`语音服务请求失败（HTTP ${response.status}）`);
        const payload = await response.text();
        if (Buffer.byteLength(payload, 'utf8') > maxSseBytes) throw new ServiceUnavailableException('语音服务响应过大');
        return pcm16ToWav(decodeMiniMaxSse(payload));
      } catch (error) {
        if(signal?.aborted)throw new BadRequestException('朗读已停止');
        lastError = error;
        if (attempt >= this.env.MINIMAX_TTS_MAX_RETRIES) break;
      } finally { clearTimeout(timeout); }
    }
    if (lastError instanceof ServiceUnavailableException) throw lastError;
    throw new ServiceUnavailableException('连接语音服务失败，可以稍后重试或使用字幕视频');
  }
}
