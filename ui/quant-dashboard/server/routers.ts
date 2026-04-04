import { COOKIE_NAME } from "@shared/const";
import { systemRouter } from "./_core/systemRouter";
import { getSessionCookieOptions } from "./_core/cookies";
import { publicProcedure, router } from "./_core/trpc";
import { z } from "zod";
import { nanoid } from "nanoid";
import { invokeLLM } from "./_core/llm";
import * as bridge from "./pythonBridge";
import * as binance from "./binanceTestnet";
import { spawn, type ChildProcess } from "child_process";
import * as fs from "fs";
import * as path from "path";

// --- Alpha discovery process handle ---
let _discoveryProcess: ChildProcess | null = null;
const PROJECT_ROOT = path.resolve(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, "$1"), "../../..");
const SYSTEM_STATE_PATH = path.join(PROJECT_ROOT, "monitor/output/system_state.json");

function _patchDiscoveryAlive(alive: boolean) {
  try {
    const raw = fs.existsSync(SYSTEM_STATE_PATH) ? fs.readFileSync(SYSTEM_STATE_PATH, "utf8") : "{}";
    const obj = JSON.parse(raw);
    obj.discovery_alive = alive;
    fs.writeFileSync(SYSTEM_STATE_PATH, JSON.stringify(obj, null, 2));
  } catch { /* ignore */ }
}

// 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?In-memory alpha engine state (reflects Python discovery process) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂?
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

// 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?App Router 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
export const appRouter = router({
  system: systemRouter,

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Auth (local 闂?no OAuth) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return { success: true } as const;
    }),
  }),
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
      if (!cfg.hasConfig) return { success: false, message: "闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾妤犵偞鐗犻、鏇㈡晜閽樺缃曟繝鐢靛Т閿曘倗鈧凹鍣ｉ妴鍛村矗婢跺牅绨婚棅顐㈡处閹哥偓鏅堕弴銏＄厱閻忕偠顕ф慨鍌炴煛鐏炵偓绀嬬€规洜鍘ч埞鎴﹀炊瑜忛悰鈺冪磽娓氣偓缁犳牜绮婚弽顐や笉闁哄稁鍘奸拑鐔兼煥濠靛棭妲归柡鍜佸墴閺屾盯寮撮悙鍏哥驳濡炪倕楠告稉?Key", latency: 0 };
      const result = await binance.testConnection(cfg.apiKey, cfg.apiSecret);
      return result;
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Wallet 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
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

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Trading Pairs 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂?
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
      return { success: false, symbol: input.symbol, message: "闂傚倸鍊峰ù鍥х暦閻㈢绐楅柟閭﹀枛閸ㄦ繈骞栧ǎ顒€鐏繛鍛У娣囧﹪濡堕崨顔兼缂備胶濮抽崡鎶藉蓟濞戞ǚ妲堟慨妤€鐗婇弫鎯р攽閻愬弶鍣藉┑鐐╁亾闂佸搫鐭夌徊浠嬶綖濠婂牆鐒垫い鎺嗗亾妞ゎ厼娲╅ˇ鎾煃瑜滈崜娆撴倶濮樿埖鍋傞柨鐔哄Т閽冪喖鏌曢崼婵囶棡闁告瑥绻橀弻鐔兼焽閿曗偓閸樻挳鏌涚€ｎ偅灏摶鏍煕濞戝崬骞楁俊宸墴濮婅櫣绱掑鍡欏姼濡炪們鍎卞Λ婊堝Φ閹版澘绠抽柟鎯у帠濮规姊绘担鍛婂暈闁荤喆鍎佃棟妞ゆ牗鍩冮弸宥夋煏閸繍妲归柍閿嬪灴瀵爼鎮欓弶鎴闁荤姵鍔掗崡鎶藉蓟濞戙垺鏅查煫鍥ㄦ礈琚﹂梻?BTCUSDT" };
    }),
    updateStatus: publicProcedure.input(z.object({
      symbol: z.string(),
      dataDownloadProgress: z.number().optional(),
      alphaEngineStatus: z.string().optional(),
      dataCollectionStatus: z.string().optional(),
    })).mutation(() => ({ success: true })),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Strategies 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷?
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
          mechanismType: null,
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
      // Lookup table: family -> Chinese name from system_state strategies
      const zhNameMap: Record<string, string> = {};
      for (const s of (state?.strategies ?? [])) {
        if (s.family && s.name && s.name !== s.family) zhNameMap[s.family] = s.name;
      }

      const alphaStrategies = approved.filter(r => r.status === "approved").map(r => {
        const override = strategyStatusOverrides[`ALPHA-${r.id}`];
        const alphaFamily = (r as any).family ?? "";
        return {
          strategyId:   `ALPHA-${r.id}`,
          name:         zhNameMap[alphaFamily] ?? r.group ?? r.rule_str ?? r.id,
          type:         "ALPHA" as const,
          direction:    (r.entry.direction.toUpperCase() as "LONG" | "SHORT"),
          symbol:       "BTCUSDT",
          entryCondition: r.rule_str ?? "",
          exitConditionTop3: Object.entries(r.exit ?? {}).map(([key, value]) => ({ label: `${key}: ${String(value)}` })),
          oosWinRate:   r.stats.oos_win_rate,
          oosAvgReturn: r.stats.oos_avg_ret,
          mechanismType: (r as any).mechanism_type ?? null,
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

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Alpha Engine 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂?
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
      // Discovery process is managed by watchdog.py 閳?this button reflects UI intent only.
      // discovery_alive is written by run_live_discovery.py itself via _set_discovery_alive().
      globalEngineState = {
        status: "running",
        startedAt: new Date(),
        stoppedAt: null,
        currentPairs: ["BTCUSDT"],
        totalRuns: globalEngineState.totalRuns + 1,
        params: input?.params ?? globalEngineState.params,
      };
      return { success: true, pairs: ["BTCUSDT"], message: "Alpha engine started. Managed by watchdog.py." };
    }),

    stopGlobal: publicProcedure.mutation(() => {
      globalEngineState.status = "stopped";
      globalEngineState.stoppedAt = new Date();
      return { success: true, message: "Alpha engine stopped. Watchdog will restart it automatically." };
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

    // 閳光偓閳光偓 LLM Promoter Engine 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
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

    getForceLibrary: publicProcedure.query(() => {
      return bridge.getForceLibraryState();
    }),

    getRegimeStatus: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      return {
        regime: state?.regime ?? "UNKNOWN",
        price: state?.price ?? 0,
        symbol: state?.symbol ?? "BTCUSDT",
      };
    }),

    getSignalWinRates: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      if (!state?.strategies) return [];
      return state.strategies.map((s: any) => ({
        family: s.family,
        name: s.name ?? s.family,
        direction: s.direction ?? "both",
        oosWinRate: s.oos_win_rate ?? null,
        mechanismType: s.mechanism_type ?? null,
        status: s.status ?? "unknown",
      }));
    }),

    getForceConcentration: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      const positions = state?.positions ?? [];
      const forceLib = bridge.getForceLibraryState();
      const concentration: Record<string, number> = forceLib?.concentration ?? {};
      return {
        concentration,
        positionCount: positions.length,
      };
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Trades 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
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

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?System Events (from alerts.log) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€?
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

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Dev Progress (real tasks from data/dev_tasks.json) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷?
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

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?LLM Analysis (optional 闂?works if BUILT_IN_FORGE_API_KEY is set) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣?
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





