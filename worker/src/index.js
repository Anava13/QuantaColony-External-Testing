// Upload service for the QuantaColony study: receives one participant's results zip from the study page
// and commits it to the private results repository. The GitHub token lives only here, as the Worker
// secret RESULTS_TOKEN (fine-grained: Contents read and write on the results repository only).
//
//   POST /upload   body: the zip (application/zip); header X-File-Name: the zip's name
//   -> 201 {ok: true, path}   the file is at results/<date>/<time>-<id>-<name> in RESULTS_REPO

const MAX_BYTES = 25 * 1024 * 1024;

function cors(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim());
  return {
    "Access-Control-Allow-Origin": allowed.includes(origin) ? origin : allowed[0] || "",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-File-Name",
    "Vary": "Origin",
  };
}

function reply(status, body, headers) {
  return new Response(JSON.stringify(body), { status, headers: { ...headers, "Content-Type": "application/json" } });
}

function base64(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

function safeName(header) {
  let name = "results.zip";
  try { name = decodeURIComponent(header || "") || name; } catch { /* keep the default */ }
  name = name.replace(/[^A-Za-z0-9 ._-]+/g, "_").replace(/\s+/g, "_").slice(0, 120);
  return name.endsWith(".zip") ? name : name + ".zip";
}

export default {
  async fetch(request, env) {
    const headers = cors(request, env);
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });
    if (request.method !== "POST" || url.pathname !== "/upload") return reply(404, { ok: false }, headers);

    const length = Number(request.headers.get("Content-Length") || 0);
    if (length > MAX_BYTES) return reply(413, { ok: false, error: "too large" }, headers);
    const bytes = new Uint8Array(await request.arrayBuffer());
    if (bytes.length === 0 || bytes.length > MAX_BYTES) return reply(413, { ok: false, error: "size" }, headers);
    if (bytes[0] !== 0x50 || bytes[1] !== 0x4b || bytes[2] !== 0x03 || bytes[3] !== 0x04) {
      return reply(415, { ok: false, error: "not a zip file" }, headers);            // only results zips
    }

    const now = new Date();
    const stamp = now.toISOString().replace(/[:.]/g, "-");
    const id = crypto.randomUUID().slice(0, 8);
    const path = `results/${stamp.slice(0, 10)}/${stamp}-${id}-${safeName(request.headers.get("X-File-Name"))}`;
    const response = await fetch(`https://api.github.com/repos/${env.RESULTS_REPO}/contents/${path}`, {
      method: "PUT",
      headers: { "Authorization": `Bearer ${env.RESULTS_TOKEN}`, "Accept": "application/vnd.github+json",
                 "User-Agent": "quantacolony-study-upload", "X-GitHub-Api-Version": "2022-11-28" },
      body: JSON.stringify({ message: `Study results ${stamp} ${id}`, content: base64(bytes) }),
    });
    if (!response.ok) return reply(502, { ok: false, error: `GitHub ${response.status}` }, headers);
    return reply(201, { ok: true, path }, headers);
  },
};
