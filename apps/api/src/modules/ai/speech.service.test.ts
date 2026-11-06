import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadApiEnv } from '@study-agent/config';
import { buildMiniMaxRequest, decodeMiniMaxSse, pcm16ToWav, SpeechService } from './speech.service';

function sse(audio: Buffer, code = 0) {
  return `data: ${JSON.stringify({ data: { audio: audio.toString('hex'), status: code ? 1 : 2 },
    trace_id: 'speech-fixture', base_resp: { status_code: code, status_msg: code ? 'invalid api key' : 'ok' } })}\n`;
}

describe('whiteboard speech providers', () => {
  afterEach(() => {vi.unstubAllEnvs();vi.unstubAllGlobals();vi.restoreAllMocks();});

  it('ports MiniMax T2A v2 constrained PCM request settings', () => {
    const env = loadApiEnv({ MINIMAX_API_KEY: 'fixture-key' });
    const request = buildMiniMaxRequest(env, '先统一分数单位。', 'Chinese (Mandarin)_Warm_Girl');
    expect(request.audio_setting).toEqual({ sample_rate: 16_000, format: 'pcm', channel: 1 });
    expect(request.stream_options).toEqual({ exclude_aggregated_audio: true });
    expect(request.voice_setting).toMatchObject({ voice_id: 'Chinese (Mandarin)_Warm_Girl', speed: 1, pitch: 0 });
  });

  it('reassembles streamed hex PCM and wraps it as a valid WAV', () => {
    const first = Buffer.from([1, 0, 2, 0]), second = Buffer.from([3, 0]);
    const pcm = decodeMiniMaxSse(sse(first) + sse(second) + 'data: [DONE]\n');
    const wav = pcm16ToWav(pcm);
    expect(wav.subarray(0, 4).toString()).toBe('RIFF');
    expect(wav.subarray(8, 12).toString()).toBe('WAVE');
    expect(wav.readUInt32LE(24)).toBe(16_000);
    expect(wav.readUInt32LE(40)).toBe(6);
    expect(wav.subarray(44)).toEqual(pcm);
  });

  it('reports provider errors without exposing credentials', () => {
    expect(() => decodeMiniMaxSse(sse(Buffer.alloc(0), 2049))).toThrow(/2049.*invalid api key/);
    expect(() => pcm16ToWav(Buffer.from([1]))).toThrow(/PCM16/);
  });

  it('uses MiniMax ahead of OpenAI and exposes selectable Mandarin voices', async () => {
    vi.stubEnv('MINIMAX_API_KEY', 'fixture-key');
    vi.stubEnv('MINIMAX_TTS_BASE_URL', 'https://api.minimaxi.com/v1/t2a_v2');
    const pcm = Buffer.from([1, 0, 2, 0]), fetcher = vi.fn(async () => new Response(sse(pcm), { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    const service = new SpeechService();
    expect(service.capabilities()).toMatchObject({ available: true, provider: 'minimax' });
    expect(service.capabilities().voices.map(voice => voice.id)).toContain('Chinese (Mandarin)_Warm_Girl');
    const result = await service.synthesize('先统一单位。', 'Chinese (Mandarin)_Warm_Girl');
    expect(result).toMatchObject({ provider: 'minimax', contentType: 'audio/wav' });
    expect(result.content.subarray(0, 4).toString()).toBe('RIFF');
    expect(fetcher).toHaveBeenCalledOnce();
    await expect(service.synthesize('测试', 'not-a-supported-voice')).rejects.toThrow('不支持的旁白音色');
  });

  it('exposes OpenAI capabilities when MiniMax is not configured', () => {
    vi.stubEnv('MINIMAX_API_KEY', '');
    vi.stubEnv('OPENAI_API_KEY', 'fixture-openai-key');
    const service = new SpeechService();
    expect(service.capabilities()).toMatchObject({ available: true, provider: 'openai', defaultVoice: 'alloy' });
    expect(service.capabilities().voices.map(voice => voice.id)).toContain('coral');
  });
  it('sends speech to a separate OpenAI-compatible endpoint without changing the chat endpoint', async () => {
    vi.stubEnv('MINIMAX_API_KEY', '');
    vi.stubEnv('OPENAI_API_KEY', 'fixture-chat-key');
    vi.stubEnv('OPENAI_BASE_URL', 'https://chat.example.test/v1');
    vi.stubEnv('OPENAI_TTS_BASE_URL', 'http://127.0.0.1:14500/v1');
    vi.stubEnv('OPENAI_TTS_API_KEY', '');
    const requests: Array<{ url: string; authorization: string | null }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input instanceof Request ? input.url : input), authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined)).get('authorization') });
      return new Response(Buffer.from('ID3 synthetic'), { status: 200, headers: { 'content-type': 'audio/mpeg' } });
    }));
    try {
      const result = await new SpeechService().synthesize('先统一单位。', 'alloy');
      expect(result).toMatchObject({ provider: 'openai', contentType: 'audio/mpeg' });
      expect(requests).toHaveLength(1);
      expect(requests[0].url).toBe('http://127.0.0.1:14500/v1/audio/speech');
      expect(requests[0].authorization).toBe('Bearer fixture-chat-key');
    } finally { vi.unstubAllGlobals(); }
  });
  it('uses the same fixed neutral text for an OpenAI voice, validates its version and never substitutes a MiniMax asset',async()=>{
    vi.stubEnv('MINIMAX_API_KEY','');vi.stubEnv('OPENAI_API_KEY','fixture-openai-key');const service=new SpeechService(),preview=service.capabilities().preview!;
    const synthesis=vi.spyOn(service,'synthesize').mockResolvedValue({content:Buffer.from('synthetic OpenAI audio'),contentType:'audio/mpeg',provider:'openai'}),controller=new AbortController();
    try{
      expect(preview.model).toBeNull();await service.preview('coral',preview.version,controller.signal);expect(synthesis).toHaveBeenCalledWith(preview.text,'coral',controller.signal);
      await expect(service.preview('coral','old-sample',controller.signal)).rejects.toThrow('音色或试听版本已变化');
      await expect(service.preview('Chinese (Mandarin)_Warm_Girl',preview.version,controller.signal)).rejects.toThrow('音色或试听版本已变化');expect(synthesis).toHaveBeenCalledOnce();
    }finally{synthesis.mockRestore();}
  });
  it('forwards cancellation to MiniMax and stops retries instead of continuing a cancelled sentence',async()=>{
    vi.stubEnv('MINIMAX_API_KEY','fixture-key');vi.stubEnv('MINIMAX_TTS_BASE_URL','https://api.minimaxi.com/v1/t2a_v2');vi.stubEnv('MINIMAX_TTS_MAX_RETRIES','2');
    let entered!:()=>void;const arrival=new Promise<void>(resolve=>{entered=resolve;}),fetcher=vi.fn((_url:unknown,options:any)=>new Promise<Response>((_resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});entered();}));vi.stubGlobal('fetch',fetcher);const service=new SpeechService(),controller=new AbortController(),pending=service.synthesize('Keep this exact sentence.',undefined,controller.signal);const rejected=expect(pending).rejects.toThrow('朗读已停止');await arrival;controller.abort();await rejected;expect(fetcher).toHaveBeenCalledOnce();
  });
  it('rejects oversized or already cancelled text before invoking a speech provider',async()=>{
    vi.stubEnv('MINIMAX_API_KEY','fixture-key');const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);const service=new SpeechService(),controller=new AbortController();controller.abort();await expect(service.synthesize('Test',undefined,controller.signal)).rejects.toThrow('朗读已停止');await expect(service.synthesize('a'.repeat(4097))).rejects.toThrow('4096');expect(fetcher).not.toHaveBeenCalled();
  });
});
