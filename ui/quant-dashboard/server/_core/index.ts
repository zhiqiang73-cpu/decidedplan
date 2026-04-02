// Load .env from the ui/quant-dashboard directory (works regardless of cwd)
import { config as loadDotenv } from "dotenv";
import { resolve as resolvePath, dirname as pathDirname } from "path";
import { fileURLToPath } from "url";
import fs from "fs";
const __uiDir = pathDirname(pathDirname(fileURLToPath(import.meta.url)));  // dist/ 鈫?ui/quant-dashboard/
loadDotenv({ path: resolvePath(__uiDir, ".env"), override: false });
loadDotenv({ path: resolvePath(process.cwd(), ".env"), override: false }); // also load root .env (Binance keys)

// 鈹€鈹€鈹€ Global crash handlers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
const _crashLog = resolvePath(process.cwd(), "monitor/output/processes/ui_crash.log");
function _logCrash(type: string, err: unknown) {
  const msg = `[${new Date().toISOString()}] ${type}: ${err instanceof Error ? err.stack : String(err)}\n`;
  console.error(msg);
  try { fs.mkdirSync(resolvePath(process.cwd(), "monitor/output/processes"), { recursive: true }); } catch {}
  try { fs.appendFileSync(_crashLog, msg, "utf8"); } catch {}
}
process.on("uncaughtException",  (err) => { _logCrash("uncaughtException",  err); process.exit(1); });
process.on("unhandledRejection", (err) => { _logCrash("unhandledRejection", err); process.exit(1); });

import express from "express";
import { createServer, type Server } from "http";
import net from "net";
import { createExpressMiddleware } from "@trpc/server/adapters/express";
import { appRouter } from "../routers";
import { createContext } from "./context";
import { serveStatic, setupVite } from "./vite";
import { initWebSocket } from "../wsServer";

function ensurePreferredPort(server: Server, port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };

    const onError = (error: NodeJS.ErrnoException) => {
      server.off("listening", onListening);
      if (error.code === "EADDRINUSE") {
        reject(new Error(`Port ${port} is already in use. Stop the existing process that is occupying ${port} before starting Quant Dashboard.`));
        return;
      }
      reject(error);
    };

    server.once("listening", onListening);
    server.once("error", onError);
    server.listen(port);
  });
}

async function startServer() {
  const app = express();
  const server = createServer(app);

  app.use(express.json({ limit: "50mb" }));
  app.use(express.urlencoded({ limit: "50mb", extended: true }));

  // tRPC API
  app.use(
    "/api/trpc",
    createExpressMiddleware({ router: appRouter, createContext })
  );

  // WebSocket (Socket.io)
  initWebSocket(server);

  if (process.env.NODE_ENV === "development") {
    await setupVite(app, server);
  } else {
    serveStatic(app);
  }

  const preferredPort = Number.parseInt(process.env.PORT || "3000", 10);
  await ensurePreferredPort(server, preferredPort);
  console.log(`[UI] Quant Dashboard running on http://localhost:${preferredPort}/`);
}

startServer().catch(error => {
  console.error(error);
  process.exit(1);
});
