// Local user management for the sign-in (BLA-60). Run from frontend/:
//   npm run auth:secret              print a new BLANC_AUTH_SECRET for the repo-root .env
//   npm run auth:add-user -- <name>  add or reset a user (prompts for the password)
//   npm run auth:remove-user -- <name>
// Users are stored as scrypt hashes in .blanc/users.json at the repo root (gitignored),
// or BLANC_USERS_FILE. Passwords are never printed or stored in plain text.
import { randomBytes } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { stdin, stdout } from "node:process";
import { hashPassword, readUsers } from "../src/lib/auth";

const repoRoot = path.resolve(process.cwd(), "..");
const usersFile = path.resolve(repoRoot, process.env.BLANC_USERS_FILE || path.join(".blanc", "users.json"));

function save(users: { username: string; password_hash: string }[]) {
  mkdirSync(path.dirname(usersFile), { recursive: true });
  writeFileSync(usersFile, `${JSON.stringify({ users }, null, 2)}\n`, { mode: 0o600 });
}

async function readHidden(prompt: string): Promise<string> {
  if (!stdin.isTTY) {
    // Piped input (scripts, CI): first line is the password.
    let data = "";
    for await (const chunk of stdin) data += chunk;
    return data.split(/\r?\n/)[0];
  }
  stdout.write(prompt);
  stdin.setRawMode(true);
  stdin.resume();
  stdin.setEncoding("utf8");
  return new Promise((resolve, reject) => {
    let value = "";
    const onData = (key: string) => {
      for (const ch of key) {
        if (ch === "\r" || ch === "\n") {
          stdin.setRawMode(false); stdin.pause(); stdin.off("data", onData); stdout.write("\n");
          return resolve(value);
        }
        if (ch === "\u0003") { stdin.setRawMode(false); stdout.write("\n"); return reject(new Error("cancelled")); }
        if (ch === "\u007f") value = value.slice(0, -1);
        else value += ch;
      }
    };
    stdin.on("data", onData);
  });
}

async function main() {
  const [command, name] = process.argv.slice(2);
  if (command === "secret") {
    console.log(`BLANC_AUTH_SECRET=${randomBytes(32).toString("base64url")}`);
    return;
  }
  const username = name?.trim();
  if ((command !== "add" && command !== "remove") || !username) {
    console.error("usage: auth-user.ts secret | add <username> | remove <username>");
    process.exit(2);
  }
  const users = readUsers(usersFile).filter(u => u.username !== username);
  if (command === "remove") {
    save(users);
    console.log(`Removed ${username} (if present) from ${usersFile}`);
    return;
  }
  const password = await readHidden(`Password for ${username}: `);
  if (password.length < 8) {
    console.error("Password must be at least 8 characters.");
    process.exit(1);
  }
  if (stdin.isTTY && (await readHidden("Repeat password: ")) !== password) {
    console.error("Passwords do not match.");
    process.exit(1);
  }
  save([...users, { username, password_hash: hashPassword(password) }]);
  console.log(`Saved ${username} to ${usersFile}`);
}

main().catch(error => { console.error(error instanceof Error ? error.message : error); process.exit(1); });
