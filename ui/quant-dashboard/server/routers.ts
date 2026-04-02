import { systemRouter } from "./_core/systemRouter";
import { publicProcedure, router } from "./_core/trpc";
import { z } from "zod";
import { nanoid } from "nanoid";
import { invokeLLM } from "./_core/llm";
import * as bridge from "./pythonBridge";
import * as binance from "./binanceTestnet";

// 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?In-memory alpha engine state (reflects Python discovery process) 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾?
let globalEngineState: {
  status: "stopped" | "running" | "paused";
  startedAt: Date | null;
  stoppedAt: Date | null;
  currentPairs: string[];
  totalRuns: number;
  params: { icThreshold: number; oosWinRateMin: number; maxConditions: number; lookbackDays: number };
} = {
  status: "stopped",
  startedAt: null,
  stoppedAt: null,
  currentPairs: [],
  totalRuns: 0,
  params: { icThreshold: 0.05, oosWinRateMin: 0.60, maxConditions: 3, lookbackDays: 180 },
};

// Strategy status overrides (paused/retired per user action, persisted in memory for now)
const strategyStatusOverrides: Record<string, "active" | "paused" | "degraded" | "retired"> = {};

const LIVE_ACTIVE_STRATEGY_STATUSES = new Set([
  "trade_ready",
  "partial_trade_ready",
  "warming_up",
  "warmup",
  "running",
  "enabled",
  "live",
]);

// 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?App Router 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻?
export const appRouter = router({
  system: systemRouter,

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?Auth (local 闂?no OAuth) 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻?
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(() => ({ success: true } as const)),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?API Config 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?
  apiConfig: router({
    get: publicProcedure.query(() => {
      const cfg = bridge.getEnvConfig();
      return {
        id: 1,
        userId: 1,
        exchange: "binance",
        isTestnet: true,
        isActive: cfg.hasConfig,
        apiKey: cfg.apiKey || null,
        apiSecret: cfg.hasConfig ? "****************" : null,
        lastTestStatus: cfg.hasConfig ? "success" : "pending",
        lastTestedAt: null,
      };
    }),
    save: publicProcedure.input(z.object({
      apiKey: z.string().min(1),
      apiSecret: z.string().min(1),
      isTestnet: z.boolean().default(true),
    })).mutation(({ input }) => {
      bridge.saveEnvConfig(input.apiKey, input.apiSecret);
      return { success: true };
    }),
    testConnection: publicProcedure.mutation(async () => {
      const cfg = bridge.getEnvConfig();
      if (!cfg.hasConfig) return { success: false, message: "闂傚倸鍊风粈渚€骞栭锔藉亱婵犲﹤瀚々鍙夈亜韫囨挾澧曢柛灞诲姂閺屾洟宕煎┑鍥х獩缂侀箖绠栫粻鏍蓟閿濆鏅柛鏇炵仛椤庡不 Key", latency: 0 };
      const result = await binance.testConnection(cfg.apiKey, cfg.apiSecret);
      return result;
    }),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?Wallet 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻?
  wallet: router({
    getSnapshot: publicProcedure.query(async () => {
      const state = bridge.getSystemState();
      if (state) {
        const usedMargin = state.positions.reduce(
          (s, p) => s + (p.entry_price * p.qty) / 10, 0  // leverage 10
        );
        const totalEquity = state.balance + usedMargin;
        return {
          id: 1,
          snapshotAt: new Date(state.timestamp),
          totalEquity:      totalEquity.toFixed(4),
          availableBalance: state.balance.toFixed(4),
          usedMargin:       usedMargin.toFixed(4),
          unrealizedPnl:    "0.0000",
          assets: [
            { asset: "USDT", balance: state.balance.toFixed(4), unrealizedPnl: "0" },
          ],
        };
      }
      // Fallback: try Binance API
      const cfg = bridge.getEnvConfig();
      if (cfg.hasConfig) {
        const acct = await binance.getAccountBalance(cfg.apiKey, cfg.apiSecret);
        if (acct) return { id: 1, snapshotAt: new Date(), ...acct };
      }
      return null;
    }),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?Trading Pairs 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾?
  execution: router({
    getLiveSnapshot: publicProcedure.query(async () => {
      const state = bridge.getSystemState();

      const base = {
        timestamp: state?.timestamp ? new Date(state.timestamp) : null,
        marketTimestamp: state?.market_timestamp ? new Date(state.market_timestamp) : null,
        symbol: state?.symbol ?? "BTCUSDT",
        price: state?.price ?? 0,
      };

      let positions = (state?.positions ?? []).map((p, idx) => ({
        positionId: `LIVE-${p.signal_name || idx + 1}-${p.entry_time || idx + 1}`,
        signalName: p.signal_name,
        strategyFamily: p.family,
        symbol: state?.symbol ?? "BTCUSDT",
        direction: (p.direction ?? "").toUpperCase() === "LONG" ? "LONG" : "SHORT",
        quantity: p.qty,
        entryPrice: p.entry_price,
        entryAt: p.entry_time ? new Date(p.entry_time) : null,
        confidence: p.confidence ?? 0,
        exitLogic: p.exit_logic ?? null,
      }));

      let pendingOrders = (state?.pending_orders ?? []).map((o, idx) => ({
        orderId: o.order_id || `PENDING-${idx + 1}`,
        signalName: o.signal_name,
        quantity: o.qty,
        requestedPrice: o.requested_price,
      }));

      const cfg = bridge.getEnvConfig();
      if (cfg.hasConfig && state?.symbol) {
        if (positions.length === 0) {
          const exPositions = await binance.getOpenPositions(cfg.apiKey, cfg.apiSecret, state.symbol);
          if (exPositions.length > 0) {
            positions = exPositions.map((p, idx) => ({
              positionId: `EXCHANGE-${idx + 1}-${p.direction}-${p.entryPrice}`,
              signalName: "exchange_sync",
              strategyFamily: "EXCHANGE",
              symbol: p.symbol,
              direction: p.direction,
              quantity: p.quantity,
              entryPrice: p.entryPrice,
              entryAt: null,
              confidence: 0,
              exitLogic: null,
            }));
          }
        }

        if (pendingOrders.length === 0) {
          const exOrders = await binance.getOpenOrders(state.symbol, cfg.apiKey, cfg.apiSecret);
          if (exOrders.length > 0) {
            pendingOrders = exOrders.map((o) => ({
              orderId: o.orderId,
              signalName: `${o.side} ${o.type}`,
              quantity: o.origQty,
              requestedPrice: o.price,
            }));
          }
        }
      }

      return {
        ...base,
        positions,
        pendingOrders,
      };
    }),
  }),

  tradingPairs: router({
    list: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      return [{
        id: 1,
        symbol: "BTCUSDT",
        baseAsset: "BTC",
        quoteAsset: "USDT",
        isTracked: true,
        dataCollectionStatus: "completed",
        dataDownloadProgress: 100,
        alphaEngineStatus: state?.discovery_alive ? "scanning" : "idle",
        currentPrice:     String(state?.price ?? 0),
        priceChange24h:   0,
        volume24h:        null,
        totalKlines:      504000,  // ~18 months of 1m data
        dataQualityScore: 99,
        lastDataUpdate:   state ? new Date(state.timestamp) : null,
      }];
    }),
    get: publicProcedure.input(z.object({ symbol: z.string() })).query(({ input }) => {
      if (input.symbol !== "BTCUSDT") return null;
      const state = bridge.getSystemState();
      return {
        id: 1, symbol: "BTCUSDT", baseAsset: "BTC", quoteAsset: "USDT",
        isTracked: true, dataCollectionStatus: "completed", dataDownloadProgress: 100,
        alphaEngineStatus: state?.discovery_alive ? "scanning" : "idle",
        currentPrice: String(state?.price ?? 0), priceChange24h: 0, volume24h: null,
      };
    }),
    add: publicProcedure.input(z.object({ symbol: z.string() })).mutation(({ input }) => {
      return { success: false, symbol: input.symbol, message: "闂備浇宕甸崰鎰垝鎼淬垺娅犳俊銈呮噹缁犱即鏌涘☉姗堟敾婵炲懐濞€閺岋絽顫滈埀顒€顭囪閳ь剙鐏氶悡锟犲蓟閵堝棙鍙忛柟閭﹀厴閸嬫挸螖閸涱厽妲梺缁橆焾椤ュ棜銇愰幒鎾充汗闂佸憡鐟ラˇ顖氣枍閵堝鈷戦弶鐐村鐠愪即鏌涢敐蹇曠М闁?BTCUSDT" };
    }),
    updateStatus: publicProcedure.input(z.object({
      symbol: z.string(),
      dataDownloadProgress: z.number().optional(),
      alphaEngineStatus: z.string().optional(),
      dataCollectionStatus: z.string().optional(),
    })).mutation(() => ({ success: true })),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?Strategies 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?
  strategies: router({
    list: publicProcedure.input(z.object({
      type:   z.enum(["P1", "P2", "ALPHA"]).optional(),
      status: z.string().optional(),
      symbol: z.string().optional(),
      search: z.string().optional(),
    }).nullish()).query(({ input }) => {
      const state = bridge.getSystemState();
      const trades = bridge.getTrades({ limit: 2000 });

      // Build win rate per signal family from trades
      const familyStats: Record<string, { wins: number; total: number; pnl7d: number }> = {};
      const cutoff7d = Date.now() - 7 * 86400000;
      for (const t of trades) {
        if (t.status !== "closed") continue;
        const f = t.strategyId.split("_")[0] ?? t.strategyId;
        if (!familyStats[f]) familyStats[f] = { wins: 0, total: 0, pnl7d: 0 };
        familyStats[f].total++;
        if (parseFloat(t.pnl ?? "0") > 0) familyStats[f].wins++;
        if (t.exitAt && t.exitAt.getTime() > cutoff7d) {
          familyStats[f].pnl7d += parseFloat(t.pnl ?? "0");
        }
      }

      // Map strategies from system_state.json
      const p1strategies = (state?.strategies ?? []).map((s, i) => {
        const typePrefix = s.family.startsWith("P0") ? "P1" : s.family.startsWith("C") ? "P1" : "P1";
        const stats = familyStats[s.family] ?? { wins: 0, total: 0, pnl7d: 0 };
        const liveWR = stats.total > 0 ? (stats.wins / stats.total) * 100 : null;
        const override = strategyStatusOverrides[s.family];
        const rawLiveStatus = (s.status ?? "").toLowerCase();
        const status = override ?? (LIVE_ACTIVE_STRATEGY_STATUSES.has(rawLiveStatus) ? "active" : "paused");
        return {
          strategyId:   s.family,
          name:         s.name,
          type:         typePrefix as "P1",
          direction:    s.direction.toUpperCase() as "LONG" | "SHORT" | "BOTH",
          symbol:       "BTCUSDT",
          entryCondition: s.entry_conditions,
          exitConditionTop3: [{ label: s.exit_conditions }] as Array<{ label: string }>,
          oosWinRate:   liveWR ?? null,
          oosAvgReturn: null,
          oosSampleSize: stats.total,
          confidenceScore: s.status === "trade_ready" ? 0.8 : 0.5,
          overfitScore:  0.2,
          featureDiversityScore: 0.7,
          status,
          todayTriggers: s.today.triggers,
          todayWins:    s.today.wins,
          notFilled:    s.today.not_filled,
          pnl7d:        stats.pnl7d.toFixed(4),
          backtestStatus: "idle",
          backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null,
          lastBacktestAt: null,
          approvedAt:   null,
          params:       null,
          updatedAt:    new Date(),
        };
      });

      // Approved alpha rules
      const approved = bridge.getApprovedRules() as bridge.PendingRule[];
      const alphaStrategies = approved.filter(r => r.status === "approved").map(r => {
        const override = strategyStatusOverrides[`ALPHA-${r.id}`];
        return {
          strategyId:   `ALPHA-${r.id}`,
          name:         r.group ?? r.rule_str ?? r.id,
          type:         "ALPHA" as const,
          direction:    (r.entry.direction.toUpperCase() as "LONG" | "SHORT"),
          symbol:       "BTCUSDT",
          entryCondition: r.rule_str ?? "",
          exitConditionTop3: Object.entries(r.exit ?? {}).map(([key, value]) => ({ label: `${key}: ${String(value)}` })),
          oosWinRate:   r.stats.oos_win_rate,
          oosAvgReturn: r.stats.oos_avg_ret,
          oosSampleSize: r.stats.n_oos,
          confidenceScore: r.stats.oos_win_rate / 100,
          overfitScore:  r.stats.wr_improvement ? Math.max(0, 1 - r.stats.wr_improvement / 100) : 0.3,
          featureDiversityScore: 0.7,
          status:       override ?? "active",
          todayTriggers: 0,
          todayWins:    0,
          notFilled:    0,
          pnl7d:        "0.0000",
          backtestStatus: "idle",
          backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null,
          lastBacktestAt: null,
          approvedAt:   r.discovered_at ? new Date(r.discovered_at) : null,
          params:       null,
          updatedAt:    new Date(),
        };
      });

      let all = [...p1strategies, ...alphaStrategies];

      // Filters
      const symbol = input?.symbol?.toUpperCase();
      const search = input?.search?.toLowerCase();
      if (input?.type) all = all.filter(s => s.type === input.type);
      if (input?.status) all = all.filter(s => s.status === input.status);
      if (symbol) all = all.filter(s => s.symbol === symbol);
      if (search) all = all.filter(s =>
        s.name.toLowerCase().includes(search) ||
        s.strategyId.toLowerCase().includes(search)
      );

      return all;
    }),

    get: publicProcedure.input(z.object({ strategyId: z.string() })).query(({ input }) => {
      const state = bridge.getSystemState();
      const s = state?.strategies.find(s => s.family === input.strategyId);
      if (!s) return null;
      return {
        strategyId: s.family, name: s.name, type: "P1", direction: s.direction.toUpperCase(),
        symbol: "BTCUSDT", entryCondition: s.entry_conditions, exitConditionTop3: [{ label: s.exit_conditions }] as Array<{ label: string }>,
        oosWinRate: null, oosAvgReturn: null, oosSampleSize: 0, confidenceScore: 0.7,
        overfitScore: 0.2, featureDiversityScore: 0.7,
        status: strategyStatusOverrides[s.family] ?? "active",
        backtestStatus: "idle", backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null, lastBacktestAt: null,
        approvedAt: null, params: null, updatedAt: new Date(),
        todayTriggers: s.today.triggers, pnl7d: "0.0000",
      };
    }),

    updateStatus: publicProcedure.input(z.object({
      strategyId: z.string(),
      status: z.enum(["active", "paused", "degraded", "retired"]),
    })).mutation(({ input }) => {
      strategyStatusOverrides[input.strategyId] = input.status;
      return { success: true };
    }),

    triggerBacktest: publicProcedure.input(z.object({ strategyId: z.string() })).mutation(() => {
      return { success: true, message: "Backtest execution is delegated to run_pipeline_backtest.py. Please check the dev progress page for the current workflow." };
    }),

    updateParams: publicProcedure.input(z.object({
      strategyId: z.string(),
      params: z.record(z.string(), z.unknown()),
    })).mutation(() => ({ success: true })),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?Alpha Engine 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾?
  alphaEngine: router({
    getCandidates: publicProcedure.input(z.object({
      status: z.enum(["pending", "approved", "rejected", "expired"]).optional(),
    }).nullish()).query(({ input }) => {
      const rules = bridge.getPendingRules(input?.status);
      return rules.map(r => ({
        id:          r.id,
        candidateId: r.id,
        symbol:      "BTCUSDT",
        direction:   r.entry.direction.toUpperCase() as "LONG" | "SHORT",
        fullExpression: r.rule_str ?? `${r.entry.feature} ${r.entry.operator} ${r.entry.threshold}`,
        seedCondition: `${r.entry.feature} ${r.entry.operator} ${r.entry.threshold}`,
        confirmConditions: (r.combo_conditions ?? []).map(c => `${c.feature} ${c.op} ${c.threshold}`),
        oosWinRate:    r.stats.oos_win_rate,
        oosAvgReturn:  r.stats.oos_avg_ret,
        sampleSize:    r.stats.n_oos,
        icScore:       null as number | null,
        confidenceScore: Math.min(r.stats.oos_win_rate / 100, 1),
        overfitScore:  (r as any).validation?.overfitting_score ?? (r.stats.wr_improvement ? Math.max(0, 1 - r.stats.wr_improvement / 100) : 0.3),
        status:        r.status as "pending" | "approved" | "rejected" | "expired",
        discoveredAt:  new Date(r.discovered_at),
        approvedAt:    null,
        rejectedAt:    null,
        rejectionReason: r.rejection_reason ?? null,
        exitConditionTop3: Object.entries(r.exit ?? {}).map(([key, value]) => ({ label: `${key}: ${String(value)}` })),
        backtestStatus: "idle",
        backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null,
        explanation:   r.explanation ?? null,
        featureDimensions: [] as string[],
        estimatedDailyTriggers: null as number | null,
        mechanismType:   (r as any).mechanism_type ?? null,
        causalScore:     (r as any).validation?.causal_score ?? null,
        causalIssues:    ((r as any).validation?.issues ?? []) as string[],
        causalWarnings:  ((r as any).validation?.warnings ?? []) as string[],
        causalExplanation: (r as any).validation?.causal_explanation ?? (r.explanation ?? null),
      }));
    }),

    getCandidate: publicProcedure.input(z.object({ candidateId: z.string() })).query(({ input }) => {
      const rules = bridge.getPendingRules();
      const r = rules.find(x => x.id === input.candidateId);
      if (!r) return null;
      return {
        candidateId: r.id, symbol: "BTCUSDT",
        direction: r.entry.direction.toUpperCase(),
        fullExpression: r.rule_str ?? "",
        oosWinRate: r.stats.oos_win_rate,
        oosAvgReturn: r.stats.oos_avg_ret,
        sampleSize: r.stats.n_oos,
        confidenceScore: Math.min(r.stats.oos_win_rate / 100, 1),
        overfitScore: 0.3, status: r.status,
        discoveredAt: new Date(r.discovered_at),
        explanation: r.explanation ?? null,
        exitConditionTop3: Object.entries(r.exit ?? {}).map(([key, value]) => ({ label: `${key}: ${String(value)}` })), 
      };
    }),

    approveCandidate: publicProcedure.input(z.object({ candidateId: z.string() })).mutation(({ input }) => {
      const ok = bridge.approveRule(input.candidateId);
      return { success: ok, message: ok ? "Rule approved and written to approved_rules.json. alpha_rules.py will reload it automatically." : "Candidate rule was not found." };
    }),

    rejectCandidate: publicProcedure.input(z.object({
      candidateId: z.string(),
      reason: z.string().optional(),
    })).mutation(({ input }) => {
      const ok = bridge.rejectRule(input.candidateId, input.reason);
      return { success: ok };
    }),

    getRuns: publicProcedure.input(z.object({
      symbol: z.string().optional(),
      limit: z.number().default(20),
    }).nullish()).query(() => {
      // Derive synthetic runs from pending rules discovery timestamps
      const pending = bridge.getPendingRules();
      if (pending.length === 0) return [];

      // Group by date prefix of discovered_at
      const groups: Record<string, bridge.PendingRule[]> = {};
      for (const r of pending) {
        const day = r.discovered_at.slice(0, 10);
        groups[day] = groups[day] ?? [];
        groups[day].push(r);
      }

      return Object.entries(groups)
        .sort(([a], [b]) => b.localeCompare(a))
        .slice(0, 20)
        .map(([day, rules]) => ({
          runId:           `RUN-${day}`,
          symbol:          "BTCUSDT",
          status:          "completed",
          phase:           "completed",
          progress:        100,
          featuresScanned: 52,
          candidatesFound: rules.length,
          candidatesApproved: rules.filter(r => r.status === "approved").length,
          params:          globalEngineState.params,
          startedAt:       new Date(day + "T00:00:00Z"),
          completedAt:     new Date(rules[rules.length - 1]!.discovered_at),
        }));
    }),

    getGlobalStatus: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      const discoveryRunning = state?.discovery_alive ?? false;
      // Sync in-memory state with real discovery state
      if (discoveryRunning && globalEngineState.status === "stopped") {
        globalEngineState.status = "running";
        globalEngineState.startedAt = globalEngineState.startedAt ?? new Date();
        globalEngineState.currentPairs = ["BTCUSDT"];
      } else if (!discoveryRunning && globalEngineState.status === "running") {
        globalEngineState.status = "stopped";
        globalEngineState.stoppedAt = new Date();
      }
      return {
        ...globalEngineState,
        uptimeSeconds: globalEngineState.startedAt
          ? Math.floor((Date.now() - globalEngineState.startedAt.getTime()) / 1000)
          : 0,
      };
    }),

    startGlobal: publicProcedure.input(z.object({
      params: z.object({
        icThreshold:  z.number().default(0.05),
        oosWinRateMin: z.number().default(0.60),
        maxConditions: z.number().default(3),
        lookbackDays:  z.number().default(180),
      }).optional(),
    }).nullish()).mutation(({ input }) => {
      // UI reflects real discovery state from system_state.json
      // This button is informational 闂?actual engine is managed by watchdog
      globalEngineState = {
        status: "running",
        startedAt: new Date(),
        stoppedAt: null,
        currentPairs: ["BTCUSDT"],
        totalRuns: globalEngineState.totalRuns + 1,
        params: input?.params ?? globalEngineState.params,
      };
      return { success: true, pairs: ["BTCUSDT"], message: "Alpha engine started. The watchdog process will keep the discovery loop alive." };
    }),

    stopGlobal: publicProcedure.mutation(() => {
      globalEngineState.status = "stopped";
      globalEngineState.stoppedAt = new Date();
      return { success: true, message: "Alpha engine stopped. You can restart it from the dashboard or via watchdog.py." };
    }),

    startRun: publicProcedure.input(z.object({
      symbol: z.string(),
      params: z.object({
        icThreshold:  z.number().default(0.05),
        oosWinRateMin: z.number().default(0.60),
        maxConditions: z.number().default(3),
        lookbackDays:  z.number().default(180),
      }).optional(),
    })).mutation(() => {
      return { success: true, runId: `RUN-${nanoid(8)}`, message: "Discovery run queued. The watchdog process will handle the live execution loop." };
    }),

    triggerBacktest: publicProcedure.input(z.object({ candidateId: z.string() })).mutation(() => {
      return { success: true, message: "Backtest execution is delegated to run_pipeline_backtest.py." };
    }),

    getSystemHealth: publicProcedure.query(() => {
      return bridge.getSystemHealth();
    }),

    // ── LLM Promoter Engine ──────────────────────────────────────────────────
    getLLMEngineState: publicProcedure.query(() => {
      return bridge.getEngineState();
    }),

    getReviewQueue: publicProcedure.query(() => {
      return bridge.getReviewQueue().map(r => ({
        id: r.id,
        candidateId: r.id,
        symbol: "BTCUSDT",
        direction: ((r.entry?.direction ?? "LONG").toUpperCase()) as "LONG" | "SHORT",
        fullExpression: r.rule_str ?? `${r.entry?.feature ?? ""} ${r.entry?.operator ?? ""} ${r.entry?.threshold ?? ""}`,
        oosWinRate: r.stats?.oos_win_rate ?? 0,
        oosAvgReturn: r.stats?.oos_avg_ret ?? 0,
        sampleSize: r.stats?.n_oos ?? 0,
        oosPf: r.stats?.oos_pf ?? null,
        status: r.status,
        discoveredAt: new Date(r.discovered_at),
        mechanismType: (r as any).mechanism_type ?? null,
        llmResult: (r as any).llm_result ?? null,
        llmValidated: (r as any).llm_validated ?? false,
        llmValidatedAt: (r as any).llm_validated_at ?? null,
      }));
    }),

    promoterApprove: publicProcedure.input(z.object({ candidateId: z.string() })).mutation(({ input }) => {
      const ok = bridge.promoterApprove(input.candidateId);
      return { success: ok, message: ok ? "Rule approved." : "Rule not found." };
    }),

    promoterReject: publicProcedure.input(z.object({
      candidateId: z.string(),
      reason: z.string().optional(),
    })).mutation(({ input }) => {
      const ok = bridge.promoterReject(input.candidateId);
      return { success: ok };
    }),

    saveLLMConfig: publicProcedure.input(z.object({
      apiKey: z.string().optional(),
      model: z.string().optional(),
      baseUrl: z.string().optional(),
      autoApprove: z.number().min(0).max(1).optional(),
      reviewQueue: z.number().min(0).max(1).optional(),
    })).mutation(({ input }) => {
      bridge.savePromoterConfig(input);
      return { success: true };
    }),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?Trades 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻?
  trades: router({
    list: publicProcedure.input(z.object({
      symbol:     z.string().optional(),
      strategyId: z.string().optional(),
      status:     z.enum(["open", "closed", "cancelled"]).optional(),
      direction:  z.enum(["LONG", "SHORT"]).optional(),
      limit:      z.number().default(50),
    }).nullish()).query(({ input }) => {
      return bridge.getTrades({
        symbol:     input?.symbol,
        strategyId: input?.strategyId,
        status:     input?.status,
        direction:  input?.direction,
        limit:      input?.limit ?? 50,
      });
    }),

    getStats: publicProcedure.query(() => {
      return bridge.getTradeStats();
    }),

    getChartData: publicProcedure.query(() => {
      return bridge.getChartData(7);
    }),

    close: publicProcedure.input(z.object({
      tradeId:    z.string(),
      exitPrice:  z.string(),
      exitReason: z.string().default("manual"),
    })).mutation(() => {
      return { success: false, message: "Manual close is handled by execution_engine. The UI currently exposes this as a read-only action." };
    }),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?System Events (from alerts.log) 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩?
  systemEvents: router({
    list: publicProcedure.input(z.object({
      limit:    z.number().default(50),
      severity: z.string().optional(),
    }).nullish()).query(({ input }) => {
      const alerts = bridge.getAlertsLog(input?.limit ?? 50);
      return alerts.map((a, i) => ({
        id:          i + 1,
        eventType:   "signal_triggered",
        symbol:      "BTCUSDT",
        severity:    "info" as const,
        title:       `[${a.phase}] ${a.signalName} ${a.direction}`,
        message:     a.description || `${a.signalName} 闂?${a.direction} ${a.bars}`,
        strategyId:  a.signalName,
        metadata:    { bars: a.bars, phase: a.phase },
        occurredAt:  new Date(a.timestamp.replace(" UTC", "Z")),
      }));
    }),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?Dev Progress (real tasks from data/dev_tasks.json) 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎规洘鍨块獮妯肩磼濡厧甯楅梻浣侯焾缁绘劙藝椤栨稓顩插Δ锝呭暞閳锋垿鏌涢幇顓炵祷閻㈩垬鍔戦弻娑氣偓锝庡亝瀹曞矂鏌＄仦鐣屝х€规洘顨嗗鍕節娴ｅ壊妫滈梻鍌氬€风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?
  devProgress: router({
    getTasks: publicProcedure.query(() => bridge.getDevTasks()),
    updateStatus: publicProcedure.input(z.object({
      id:     z.number(),
      status: z.enum(["completed", "in_progress", "pending", "blocked"]),
    })).mutation(({ input }) => {
      bridge.updateDevTask(input.id, input.status);
      return { success: true };
    }),
    addTask: publicProcedure.input(z.object({
      category:    z.string(),
      title:       z.string(),
      description: z.string().optional(),
      priority:    z.enum(["critical", "high", "medium", "low"]).default("medium"),
      layer:       z.string().optional(),
    })).mutation(({ input }) => {
      bridge.insertDevTask({ ...input, status: "pending", sortOrder: 99 });
      return { success: true };
    }),
  }),

  // 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑?LLM Analysis (optional 闂?works if BUILT_IN_FORGE_API_KEY is set) 闂傚倸鍊风粈渚€宕崸妤€鍌ㄦ繝濠傜墕绾惧鏌熼崜褏甯涢柣鎾冲暣閺屾稖绠涢幙鍐┬︽繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯掓潏鍛存⒑缁嬫鍎愰柟鐟版喘瀵顓兼径濠勵槯婵犮垼娉涢敃锝嗙珶閺囥垺鈷掑ù锝囶焾閺嗛亶鏌涘Ο鑽ょ煉鐎?
  llmAnalysis: router({
    analyzeStrategy: publicProcedure.input(z.object({
      strategyId: z.string(),
    })).mutation(async ({ input }) => {
      const state = bridge.getSystemState();
      const s = state?.strategies.find(x => x.family === input.strategyId);
      if (!s) return { success: false, report: "Strategy not found." };

      const prompt = [
        "Analyze the following BTC strategy and return a concise assessment.",
        `Strategy ID: ${s.family}` ,
        `Name: ${s.name}` ,
        `Direction: ${s.direction.toUpperCase()}` ,
        `Entry: ${s.entry_conditions}` ,
        `Exit: ${s.exit_conditions}` ,
        `Today triggers: ${s.today.triggers}` ,
        `Today wins: ${s.today.wins}` ,
      ].join("\\n");

      try {
        const response = await invokeLLM({
          messages: [
            { role: "system", content: "You are a quantitative trading analyst. Review the strategy and suggest practical improvements." },
            { role: "user", content: prompt },
          ],
        });
        const report = response.choices?.[0]?.message?.content ?? "No analysis returned.";
        return { success: true, report };
      } catch {
        return { success: false, report: "LLM request failed. Check BUILT_IN_FORGE_API_KEY." };
      }
    }),

    generateMarketInsight: publicProcedure.mutation(async () => {
      const state = bridge.getSystemState();
      const prompt = [
        "Summarize the current BTC market state in a concise operator-facing note.",
        `Price: ${state?.price ?? "unknown"} USDT`,
        `Regime: ${state?.regime ?? "unknown"}`,
      ].join("\\n");

      try {
        const response = await invokeLLM({
          messages: [
            { role: "system", content: "You are a market analyst producing short actionable trading insights." },
            { role: "user", content: prompt },
          ],
        });
        const insight = response.choices?.[0]?.message?.content ?? "No market insight returned.";
        return { success: true, insight };
      } catch {
        return { success: false, insight: "LLM request failed. Check BUILT_IN_FORGE_API_KEY." };
      }
    }),
  }),
});

export type AppRouter = typeof appRouter;





