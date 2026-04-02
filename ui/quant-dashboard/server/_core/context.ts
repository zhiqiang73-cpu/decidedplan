import type { CreateExpressContextOptions } from "@trpc/server/adapters/express";

// Local user — no OAuth, no database.
// This is a local quantitative trading tool; all requests are treated as admin.
export type LocalUser = {
  id: number;
  openId: string;
  name: string | null;
  email: string | null;
  role: "admin" | "user";
};

export type TrpcContext = {
  req: CreateExpressContextOptions["req"];
  res: CreateExpressContextOptions["res"];
  user: LocalUser;
};

const LOCAL_USER: LocalUser = {
  id: 1,
  openId: "local",
  name: "本地管理员",
  email: null,
  role: "admin",
};

export async function createContext(
  opts: CreateExpressContextOptions
): Promise<TrpcContext> {
  return {
    req: opts.req,
    res: opts.res,
    user: LOCAL_USER,
  };
}
