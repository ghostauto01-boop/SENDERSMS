import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  ArrowUp, Loader2, Mic, Paperclip, Square, X,
} from "lucide-react";
import { agentApi, type AgentFile } from "../../api/aiAgent";

/**
 * The message box.
 *
 * Three input methods, because operators use all three:
 *   • type — with Enter to send (Shift+Enter for a new line) and a growing box;
 *   • speak — record, send the bytes to /transcribe, drop the words in the box
 *     so they can be corrected before they are acted on;
 *   • attach — a file chip that travels with this message.
 *
 * Recording is deliberately stop-then-review: a misheard phone number that is
 * sent straight through is a message to the wrong customer.
 */

export default function AgentComposer({
  onSend,
  busy,
  onStop,
  attached,
  onDetachAttachment,
  onOpenFiles,
  disabled,
  onTranscribeFallback,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  onStop?: () => void;
  attached?: AgentFile | null;
  onDetachAttachment?: () => void;
  onOpenFiles?: () => void;
  disabled?: boolean;
  /** Browsers without MediaRecorder fall back to the OS keyboard dictation. */
  onTranscribeFallback?: () => void;
}) {
  const [text, setText] = useState("");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const timerRef = useRef<number | null>(null);
  const cancelRef = useRef(false);

  // Grow with the content, up to a point, then scroll.
  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 168)}px`;
  }, [text]);

  useEffect(() => {
    if (!recording) {
      if (timerRef.current) window.clearInterval(timerRef.current);
      timerRef.current = null;
      setElapsed(0);
      return;
    }
    timerRef.current = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [recording]);

  const stopRecording = useCallback((discard = false) => {
    cancelRef.current = discard;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }, []);

  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onTranscribeFallback?.();
      toast.error("This browser cannot record audio — use your keyboard's dictation instead.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferred = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) =>
        MediaRecorder.isTypeSupported(type),
      );
      const recorder = new MediaRecorder(stream, preferred ? { mimeType: preferred } : undefined);
      chunksRef.current = [];
      cancelRef.current = false;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        if (cancelRef.current) {
          chunksRef.current = [];
          return;
        }
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        if (blob.size < 1200) {
          toast.error("That was too short — hold the mic and speak.");
          return;
        }
        setTranscribing(true);
        try {
          const result = await agentApi.transcribe(blob, recorder.mimeType.includes("mp4") ? "voice.m4a" : "voice.webm");
          const words = (result.text || "").trim();
          if (!words) {
            toast.error("Nothing was picked up. Try again a little closer to the mic.");
          } else {
            setText((current) => (current ? `${current} ${words}` : words));
            requestAnimationFrame(() => textareaRef.current?.focus());
          }
        } catch (error: any) {
          toast.error(error?.response?.data?.detail || "Could not transcribe that recording.");
        } finally {
          setTranscribing(false);
        }
      };

      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch {
      toast.error("Microphone permission was refused. Allow it in the browser's site settings.");
    }
  };

  const submit = () => {
    const value = text.trim();
    if (!value || busy || disabled) return;
    onSend(value);
    setText("");
  };

  const canSend = Boolean(text.trim()) && !busy && !disabled && !recording && !transcribing;

  return (
    <div className="border-t border-gray-200 bg-white/95 px-2.5 pb-2.5 pt-2 backdrop-blur dark:border-gray-800 dark:bg-gray-900/95 sm:px-4">
      {attached && (
        <div className="mx-auto mb-1.5 flex max-w-3xl items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-2 py-1 text-[11.5px] dark:border-primary-800 dark:bg-primary-950/40">
          <Paperclip size={11} className="shrink-0 text-primary-700 dark:text-primary-300" />
          <span className="min-w-0 flex-1 truncate font-medium">{attached.filename}</span>
          {attached.preview?.total_rows_estimate ? (
            <span className="shrink-0 text-primary-700/70 dark:text-primary-300/70">
              {attached.preview.total_rows_estimate} rows
            </span>
          ) : null}
          <button
            type="button"
            onClick={onDetachAttachment}
            className="shrink-0 rounded p-0.5 text-primary-700 hover:bg-primary-100 dark:text-primary-300 dark:hover:bg-primary-900"
            aria-label="Remove attachment"
          >
            <X size={11} />
          </button>
        </div>
      )}

      <div className="mx-auto max-w-3xl">
        <div
          className={`flex items-end gap-1.5 rounded-2xl border bg-white px-1.5 py-1.5 shadow-sm transition-colors dark:bg-gray-800 ${
            recording
              ? "border-danger-300 dark:border-danger-700"
              : "border-gray-200 focus-within:border-primary-400 dark:border-gray-700 dark:focus-within:border-primary-700"
          }`}
        >
          <button
            type="button"
            onClick={onOpenFiles}
            className="shrink-0 rounded-xl p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
            aria-label="Files"
            title="Upload a file"
          >
            <Paperclip size={17} />
          </button>

          {recording ? (
            <div className="flex min-h-[38px] flex-1 items-center gap-2 px-1">
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-danger-400 opacity-75" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-danger-500" />
              </span>
              <span className="text-[13px] font-medium text-danger-700 dark:text-danger-400">
                Recording {String(Math.floor(elapsed / 60)).padStart(2, "0")}:
                {String(elapsed % 60).padStart(2, "0")}
              </span>
              <button
                type="button"
                onClick={() => stopRecording(true)}
                className="ml-auto shrink-0 rounded-lg px-2 py-1 text-[11.5px] font-medium text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                Discard
              </button>
            </div>
          ) : transcribing ? (
            <div className="flex min-h-[38px] flex-1 items-center gap-2 px-1 text-[13px] text-gray-500">
              <Loader2 size={14} className="animate-spin" /> Transcribing…
            </div>
          ) : (
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={1}
              placeholder={
                disabled
                  ? "Add a key to start talking to the agent…"
                  : "Ask the agent to do something…"
              }
              aria-label="Message the agent"
              className="max-h-[168px] min-h-[38px] flex-1 resize-none bg-transparent px-1 py-2 text-base leading-snug outline-none placeholder:text-gray-400 sm:text-[14.5px]"
            />
          )}

          <button
            type="button"
            onClick={() => (recording ? stopRecording(false) : startRecording())}
            disabled={transcribing || disabled}
            className={`shrink-0 rounded-xl p-2 transition-colors ${
              recording
                ? "bg-danger-600 text-white hover:bg-danger-700"
                : "text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
            } disabled:opacity-40`}
            aria-label={recording ? "Stop recording" : "Record a voice note"}
            title={recording ? "Stop and transcribe" : "Speak instead of typing"}
          >
            {recording ? <Square size={15} /> : <Mic size={17} />}
          </button>

          {busy ? (
            <button
              type="button"
              onClick={onStop}
              className="shrink-0 rounded-xl bg-gray-900 p-2 text-white hover:bg-gray-800 dark:bg-gray-600"
              aria-label="Stop"
              title="Stop the agent"
            >
              <Square size={15} />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!canSend}
              className="shrink-0 rounded-xl bg-primary-900 p-2 text-white transition-transform hover:bg-primary-800 active:scale-95 disabled:opacity-30 dark:bg-primary-700 dark:hover:bg-primary-600"
              aria-label="Send"
            >
              <ArrowUp size={17} />
            </button>
          )}
        </div>
        <p className="mt-1 hidden px-1 text-[10.5px] text-gray-400 sm:block">
          Enter sends · Shift+Enter adds a line · the mic transcribes into the box so
          you can check it first
        </p>
      </div>
    </div>
  );
}
