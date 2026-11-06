#!/usr/bin/env python3
"""studyagent-tts: small OpenAI-compatible Chinese text-to-speech service.

POST /v1/audio/speech  {model, voice, input, response_format?, speed?}  -> audio
GET  /health                                                            -> JSON

Engine: Kokoro v1.1-zh (kokoro-onnx, CPU onnxruntime) + misaki[zh] G2P
(English words via misaki's espeak-ng fallback). MP3/Opus/AAC/FLAC via ffmpeg.
Stdlib HTTP server only; binds 127.0.0.1:14500 by default.

Latency design: one request synthesizes at a time (global lock) and uses at
most TTS_THREADS (default 8) onnxruntime compute threads:
  * short input  -> sentence chunks of <=120 chars, run sequentially on a
                    session with intra_op_num_threads=TTS_THREADS
  * longer input -> chunks of ~30-60 chars run concurrently by TTS_THREADS
                    workers on a session with intra_op_num_threads=1
The Kokoro decoder scales poorly across intra-op threads, so the second mode
is ~1.4x faster for long paragraphs.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, wait
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

BASE = os.environ.get("TTS_HOME", "/data/studyagent/tts")
MODEL_DIR = os.environ.get("TTS_MODEL_DIR", os.path.join(BASE, "models"))
MODEL_FILE = os.path.join(MODEL_DIR, "kokoro-v1.1-zh.onnx")
VOICES_FILE = os.path.join(MODEL_DIR, "voices-v1.1-zh.bin")
CONFIG_FILE = os.path.join(MODEL_DIR, "config.json")
HOST = os.environ.get("TTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("TTS_PORT", "14500"))
THREADS = max(1, int(os.environ.get("TTS_THREADS", "8")))
LOCK_WAIT = float(os.environ.get("TTS_LOCK_WAIT", "15"))
PARALLEL_MIN_CHARS = int(os.environ.get("TTS_PARALLEL_MIN_CHARS", "220"))
FFMPEG = os.environ.get("TTS_FFMPEG", "/usr/bin/ffmpeg")
# onnxruntime's CPU arena never shrinks; without it, RSS returns to baseline after long requests
MEM_ARENA = os.environ.get("TTS_MEM_ARENA", "0") == "1"
MAX_INPUT_CHARS = 4096
MAX_BODY_BYTES = 256 * 1024
SAMPLE_RATE = 24000
DEFAULT_VOICE = "alloy"

# OpenAI voice name -> Kokoro v1.1-zh voice id (override with TTS_VOICE_MAP='{"alloy":"zf_xxx"}')
VOICE_MAP = {
    "alloy": "zf_001",
    "coral": "zf_002",
    "nova": "zf_003",
    "shimmer": "zf_004",
    "sage": "zm_010",
}
VOICE_MAP.update({k.lower(): v for k, v in json.loads(os.environ.get("TTS_VOICE_MAP", "{}")).items()})

# response_format -> (content type, ffmpeg output args or None for in-process)
FORMATS = {
    "mp3": ("audio/mpeg", ["-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"]),
    "wav": ("audio/wav", None),
    "pcm": ("audio/pcm", None),  # raw s16le, 24 kHz, mono (same as OpenAI)
    "opus": ("audio/ogg", ["-c:a", "libopus", "-b:a", "32k", "-f", "ogg"]),
    "aac": ("audio/aac", ["-c:a", "aac", "-b:a", "64k", "-f", "adts"]),
    "flac": ("audio/flac", ["-c:a", "flac", "-f", "flac"]),
}

logging.basicConfig(level=os.environ.get("TTS_LOG_LEVEL", "INFO"),
                    format="%(levelname)s %(message)s", stream=sys.stderr)
log = logging.getLogger("studyagent-tts")


class ApiError(Exception):
    def __init__(self, code, message, param=None, close=False, headers=None):
        super().__init__(message)
        self.code, self.message, self.param = code, message, param
        self.close, self.headers = close, headers or {}


# ---------------------------------------------------------------- text --
_FULLWIDTH = {c: c - 0xFEE0 for c in [*range(0xFF10, 0xFF1A), *range(0xFF21, 0xFF3B), *range(0xFF41, 0xFF5B)]}
_DIGIT_CN = str.maketrans("0123456789", "零一二三四五六七八九")
_SYMBOLS = str.maketrans({
    "＋": "加", "+": "加", "＝": "等于", "=": "等于", "×": "乘", "÷": "除以", "≈": "约等于",
    "≠": "不等于", "≤": "小于等于", "≥": "大于等于", "＜": "小于", "＞": "大于", "<": "小于",
    ">": "大于", "²": "的平方", "³": "的立方", "√": "根号", "π": "派", "∵": "因为", "∴": "所以",
    "°": "度", "&": "和", "~": "到", "～": "到", "→": "，", "*": "", "#": "", "`": "", "_": " ",
    "|": "，", "·": "", "•": "", "\t": " ", "\u3000": " ",
})
_CIRCLED_RE = re.compile("[\u2460-\u2473]")
_DATE_RE = re.compile(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)")
_MUL_RE = re.compile(r"(\d)\s*\*\s*(?=\d)")
_MINUS_RE = re.compile(r"(\d)\s*[-−–]\s*(?=\d)")
_NEG_RE = re.compile(r"(?<![\w.])[-−](?=\d)")
_LONGNUM_RE = re.compile(r"(?<![\d.])\d{11,}")
_LINE_END_RE = re.compile(r"([^\s。！？；…!?;.，、：,:])[ \t]*\n+")
_FALLBACK_DROP_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z，。！？；：、,.!?;:“”\"'（）() -]")
_CJK_ONLY_DROP_RE = re.compile(r"[^\u4e00-\u9fff，。！？；：、]")

_SENT_ALWAYS = set("。！？；…!?;")
_SENT_WS = set(".")            # ASCII '.' ends a sentence only before whitespace/end
_CLAUSE_ALWAYS = set("，、：")
_CLAUSE_WS = set(",:")
_CLOSERS = set("”’」』）》】)\"'")
_PAUSE_SENTENCE, _PAUSE_CLAUSE, _PAUSE_OTHER = 0.30, 0.15, 0.05


def normalize(text: str) -> str:
    """Make mixed Chinese/English/digit text friendlier for misaki zh G2P."""
    t = _LINE_END_RE.sub(r"\1。", text.replace("\r", ""))   # lines without punctuation
    t = t.translate(_FULLWIDTH)
    t = _CIRCLED_RE.sub(lambda m: str(ord(m.group()) - 0x245F), t)
    t = _DATE_RE.sub(lambda m: f"{m[1]}年{int(m[2])}月{int(m[3])}日", t)
    t = _TIME_RE.sub(lambda m: f"{int(m[1])}点" + (f"{int(m[2])}分" if int(m[2]) else ""), t)
    t = _MUL_RE.sub(r"\1乘", t)
    minus = "减" if ("=" in t or "＝" in t) else "到"
    t = _MINUS_RE.sub(lambda m: m[1] + minus, t)
    t = _NEG_RE.sub("负", t)
    t = _LONGNUM_RE.sub(lambda m: m.group().translate(_DIGIT_CN), t)  # phone numbers etc.
    t = t.translate(_SYMBOLS)
    return " ".join(t.split())


def _fallback_text(t: str) -> str:
    t = re.sub(r"(?<=\d)\.(?=\d)", "点", t).translate(_DIGIT_CN).replace("%", "")
    return " ".join(_FALLBACK_DROP_RE.sub(" ", t).split())


def _split(text, always, ws_only):
    out, start, i, n = [], 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in always or (ch in ws_only and (i + 1 == n or text[i + 1].isspace())):
            j = i + 1
            while j < n and (text[j] in always or text[j] in ws_only or text[j] in _CLOSERS):
                j += 1
            out.append(text[start:j])
            start = i = j
            continue
        i += 1
    out.append(text[start:])
    return [s.strip() for s in out if s.strip()]


def _is_word(c):
    return c.isascii() and (c.isalnum() or c in ".%'-")


def _hard_cut(s, target):
    out = []
    while len(s) > target:
        cut = target
        for j in range(target, target // 2, -1):
            if not (_is_word(s[j - 1]) and _is_word(s[j])):
                cut = j
                break
        out.append(s[:cut].strip())
        s = s[cut:].strip()
    if s:
        out.append(s)
    return [x for x in out if x]


def make_chunks(text, target):
    pieces = []
    for s in _split(text, _SENT_ALWAYS, _SENT_WS):
        if len(s) <= target:
            pieces.append(s)
            continue
        for c in _split(s, _CLAUSE_ALWAYS, _CLAUSE_WS):
            pieces.extend([c] if len(c) <= target else _hard_cut(c, target))
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) > target:
            chunks.append(cur)
            cur = ""
        cur = f"{cur} {p}" if cur and cur[-1].isascii() and p[0].isascii() else cur + p
    if cur:
        chunks.append(cur)
    return chunks


def pause_after(chunk):
    s = chunk.rstrip("".join(_CLOSERS))
    last = s[-1:] if s else ""
    if last in _SENT_ALWAYS or last in _SENT_WS:
        return _PAUSE_SENTENCE
    if last in _CLAUSE_ALWAYS or last in _CLAUSE_WS:
        return _PAUSE_CLAUSE
    return _PAUSE_OTHER


# -------------------------------------------------------------- engine --
class Engine:
    def __init__(self):
        t0 = time.monotonic()
        import jieba
        import onnxruntime as rt
        from kokoro_onnx import Kokoro
        from misaki import zh
        from misaki.espeak import EspeakFallback

        jieba.setLogLevel(logging.WARNING)
        self.rt = rt
        self.wide = Kokoro.from_session(self._session(THREADS), VOICES_FILE, vocab_config=CONFIG_FILE)
        self.narrow = Kokoro.from_session(self._session(1), VOICES_FILE, vocab_config=CONFIG_FILE)
        with np.load(VOICES_FILE) as npz:
            self.voices = {k: np.asarray(npz[k], dtype=np.float32) for k in npz.files}
        missing = sorted(set(VOICE_MAP.values()) - set(self.voices))
        if missing:
            raise RuntimeError(f"voice map refers to unknown voices: {missing}")

        espeak = EspeakFallback(british=False)

        class _Tok:
            __slots__ = ("text",)

            def __init__(self, text):
                self.text = text

        def english(word):
            try:
                ps, _ = espeak(_Tok(word))
                return ps or ""
            except Exception as e:  # never let English break Chinese synthesis
                log.warning("english g2p failed for %r: %s", word[:40], e)
                return ""

        self.zh = zh.ZHG2P(version="1.1", unk="", en_callable=english)
        vocab = self.wide.tokenizer.vocab
        self.speech_ids = {c for c in vocab if c not in set(";:,.!?/—…\"()“” ")}
        self.pool = ThreadPoolExecutor(THREADS, thread_name_prefix="synth")
        self.lock = threading.Lock()
        self.started = time.time()
        self.requests = 0
        # Warm up G2P (jieba, pypinyin, espeak) and both sessions.
        ph = self._g2p(normalize("你好，Hello！今天是2024年10月1日。"))
        self.wide.create(ph, voice=self.voices["zf_001"], is_phonemes=True)
        self.narrow.create(ph, voice=self.voices["zf_001"], is_phonemes=True)
        log.info("engine ready in %.1fs: onnxruntime %s, %d voices, threads=%d, map=%s",
                 time.monotonic() - t0, rt.__version__, len(self.voices), THREADS, VOICE_MAP)

    def _session(self, threads):
        rt = self.rt
        so = rt.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.enable_cpu_mem_arena = MEM_ARENA
        so.add_session_config_entry("session.intra_op.allow_spinning", "1" if threads > 1 else "0")
        return rt.InferenceSession(MODEL_FILE, sess_options=so, providers=["CPUExecutionProvider"])

    def resolve_voice(self, voice):
        v = voice.strip()
        if v.lower() in VOICE_MAP:
            return VOICE_MAP[v.lower()]
        for cand in (v, v.lower()):
            if cand in self.voices:
                return cand
        raise ApiError(400, f"Unsupported voice '{voice}'. Use one of: {', '.join(sorted(VOICE_MAP))} "
                            f"or a Kokoro v1.1-zh voice id such as zf_001 / zm_010.", "voice")

    def _g2p(self, chunk):
        attempts = (
            lambda: chunk,
            lambda: _fallback_text(chunk),
            lambda: _CJK_ONLY_DROP_RE.sub("", _fallback_text(chunk)),
        )
        for n, make in enumerate(attempts):
            try:
                text = make()
                if not text.strip():
                    continue
                ph, _ = self.zh(text)
                ph = " ".join((ph or "").split())
                if n:
                    log.warning("g2p fallback level %d used", n)
                return ph
            except Exception as e:
                log.warning("g2p attempt %d failed (%s: %s)", n, type(e).__name__, e)
        return ""

    def _speakable(self, ph):
        return any(c in self.speech_ids for c in ph)

    def synthesize(self, text, voice_id, speed):
        speed = min(2.0, max(0.5, float(speed)))  # Kokoro's supported range
        text = normalize(text)
        parallel = THREADS > 1 and len(text) > PARALLEL_MIN_CHARS
        target = max(40, min(60, math.ceil(len(text) / THREADS))) if parallel else 120
        t_wait = time.monotonic()
        if not self.lock.acquire(timeout=LOCK_WAIT):
            raise ApiError(503, "TTS engine is busy, please retry", headers={"Retry-After": "5"})
        try:
            t0 = time.monotonic()
            self.requests += 1
            jobs = []
            for chunk in make_chunks(text, target):
                ph = self._g2p(chunk)
                if self._speakable(ph):
                    jobs.append((ph, pause_after(chunk)))
            if not jobs:
                raise ApiError(400, "'input' contains no speakable text", "input")
            t1 = time.monotonic()
            style = self.voices[voice_id]
            mode = "parallel" if parallel and len(jobs) > 1 else "sequential"
            engine = self.narrow if mode == "parallel" else self.wide

            def run(ph):
                try:
                    return engine.create(ph, voice=style, speed=speed, is_phonemes=True)[0]
                except Exception as e:
                    log.warning("chunk synthesis failed (%s: %s); skipping", type(e).__name__, e)
                    return None

            if mode == "parallel":
                order = sorted(range(len(jobs)), key=lambda i: -len(jobs[i][0]))  # longest first
                futs = {i: self.pool.submit(run, jobs[i][0]) for i in order}
                wait(futs.values())
                audios = [futs[i].result() for i in range(len(jobs))]
            else:
                audios = [run(ph) for ph, _ in jobs]
            t2 = time.monotonic()
        finally:
            self.lock.release()
            _trim_heap()
        parts, prev_pause = [], 0.0
        for audio, (_, pause) in zip(audios, jobs):
            if audio is None or not len(audio):
                continue
            if parts:
                parts.append(np.zeros(int(prev_pause * SAMPLE_RATE), dtype=np.float32))
            parts.append(np.asarray(audio, dtype=np.float32))
            prev_pause = pause
        if not parts:
            raise RuntimeError("synthesis produced no audio")
        audio = np.concatenate(parts)
        info = {"chars": len(text), "chunks": len(jobs), "mode": mode, "wait": t0 - t_wait,
                "g2p": t1 - t0, "synth": t2 - t1, "audio": len(audio) / SAMPLE_RATE}
        return audio, info

    def health(self):
        return {"status": "ok", "model": "kokoro-v1.1-zh", "sample_rate": SAMPLE_RATE,
                "voices": VOICE_MAP, "threads": THREADS, "busy": self.lock.locked(),
                "requests": self.requests, "uptime_s": round(time.time() - self.started, 1)}


try:
    import ctypes
    _libc = ctypes.CDLL("libc.so.6")
except OSError:
    _libc = None


def _trim_heap():
    """Give freed decoder buffers back to the OS (glibc keeps them otherwise)."""
    if _libc is not None:
        try:
            _libc.malloc_trim(0)
        except Exception:
            pass


def encode(audio, fmt):
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    if fmt == "pcm":
        return pcm
    if fmt == "wav":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return buf.getvalue()
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE),
           "-ac", "1", "-i", "pipe:0", "-vn", "-ac", "1", *FORMATS[fmt][1], "pipe:1"]
    proc = subprocess.run(cmd, input=pcm, capture_output=True, timeout=60)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[-300:]}")
    return proc.stdout


def parse_request(body):
    try:
        req = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ApiError(400, f"Request body must be valid JSON: {e}")
    if not isinstance(req, dict):
        raise ApiError(400, "Request body must be a JSON object")
    text = req.get("input")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, "'input' is required and must be a non-empty string", "input")
    if len(text) > MAX_INPUT_CHARS:
        raise ApiError(400, f"'input' is too long ({len(text)} chars, max {MAX_INPUT_CHARS})", "input")
    voice = req.get("voice") or DEFAULT_VOICE
    if not isinstance(voice, str):
        raise ApiError(400, "'voice' must be a string", "voice")
    voice_id = ENGINE.resolve_voice(voice)
    fmt = req.get("response_format") or "mp3"
    if not isinstance(fmt, str) or fmt.lower() not in FORMATS:
        raise ApiError(400, f"Unsupported response_format '{fmt}'. Use one of: {', '.join(FORMATS)}",
                       "response_format")
    speed = req.get("speed")
    speed = 1.0 if speed is None else speed
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) \
            or not 0.25 <= speed <= 4.0:
        raise ApiError(400, "'speed' must be a number between 0.25 and 4.0", "speed")
    return {"input": text, "voice": voice, "voice_id": voice_id, "format": fmt.lower(), "speed": float(speed)}


# ---------------------------------------------------------------- http --
class Handler(BaseHTTPRequestHandler):
    server_version = "studyagent-tts/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info("http %s", fmt % args)

    def _send(self, code, body, ctype, headers=None, close=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, **kw):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", **kw)

    def _error(self, code, message, param=None, **kw):
        etype = "invalid_request_error" if code < 500 else ("server_busy" if code == 503 else "server_error")
        self._json(code, {"error": {"message": message, "type": etype, "param": param, "code": None}}, **kw)

    def _path(self):
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    def _read_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            data = bytearray()
            try:
                while True:
                    size = int(self.rfile.readline(1024).split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                            pass
                        return bytes(data)
                    if len(data) + size > MAX_BODY_BYTES:
                        raise ApiError(413, "Request body too large", close=True)
                    data += self.rfile.read(size)
                    self.rfile.readline(1024)
            except ValueError:
                raise ApiError(400, "Malformed chunked body", close=True)
        length = self.headers.get("Content-Length")
        if length is None:
            raise ApiError(411, "Content-Length required", close=True)
        try:
            n = int(length)
        except ValueError:
            raise ApiError(400, "Invalid Content-Length", close=True)
        if n < 0 or n > MAX_BODY_BYTES:
            raise ApiError(413, f"Request body too large (max {MAX_BODY_BYTES} bytes)", close=True)
        return self.rfile.read(n)

    def do_GET(self):
        path = self._path()
        if path in ("/health", "/healthz"):
            self._json(200, ENGINE.health())
        else:
            self._error(404, f"Unknown endpoint: GET {path}")

    def do_POST(self):
        t0 = time.monotonic()
        try:
            path = self._path()
            body = self._read_body()
            if path != "/v1/audio/speech":
                raise ApiError(404, f"Unknown endpoint: POST {path}")
            req = parse_request(body)
            audio, info = ENGINE.synthesize(req["input"], req["voice_id"], req["speed"])
            t1 = time.monotonic()
            data = encode(audio, req["format"])
            t2 = time.monotonic()
            self._send(200, data, FORMATS[req["format"]][0], headers={
                "X-Audio-Duration": f"{info['audio']:.2f}", "X-Processing-Time": f"{t2 - t0:.2f}"})
            log.info("speech voice=%s(%s) fmt=%s speed=%.2f chars=%d chunks=%d mode=%s wait=%.2fs "
                     "g2p=%.2fs synth=%.2fs enc=%.2fs total=%.2fs audio=%.1fs bytes=%d",
                     req["voice"], req["voice_id"], req["format"], req["speed"], info["chars"],
                     info["chunks"], info["mode"], info["wait"], info["g2p"], info["synth"],
                     t2 - t1, t2 - t0, info["audio"], len(data))
        except ApiError as e:
            log.info("speech rejected %d: %s", e.code, e.message)
            self._error(e.code, e.message, e.param, headers=e.headers, close=e.close)
        except (BrokenPipeError, ConnectionResetError):
            log.warning("client disconnected after %.2fs", time.monotonic() - t0)
        except Exception as e:
            log.exception("speech failed")
            try:
                self._error(500, f"Speech synthesis failed: {type(e).__name__}: {e}", close=True)
            except Exception:
                pass


ENGINE: Engine | None = None


def main():
    global ENGINE
    ENGINE = Engine()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    log.info("listening on http://%s:%d", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
