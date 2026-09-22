/**
 * Meme War frontend.
 *
 * This is a real client of the Intelligent Contract: every read and every write
 * goes through genlayer-js to the deployed `MemeWar` contract. There is no
 * server and no build step — `python -m http.server` is enough to run it.
 *
 * genlayer-js is pulled from a CDN as an ES module. Pin the version for
 * anything you intend to keep running.
 */

import { createClient } from "https://esm.sh/genlayer-js";
import { testnetAsimov, testnetBradbury } from "https://esm.sh/genlayer-js/chains";

// Bradbury is what `deploy/deploy.py` targets by default. Asimov and Bradbury
// both report chain id 4221, but they index different consensus state, so a
// contract deployed through one is genuinely absent from the other - pointing
// the frontend at the wrong one fails with "contract not found" rather than
// silently reading a different ledger.
const CHAINS = { bradbury: testnetBradbury, asimov: testnetAsimov };

const params = new URLSearchParams(location.search);
const CHAIN_KEY = (params.get("chain") || "bradbury").toLowerCase();
const CHAIN = CHAINS[CHAIN_KEY] || testnetBradbury;
const CHAIN_LABEL = CHAIN.name || `GenLayer ${CHAIN_KEY}`;

// Address from ?address=, otherwise from the deployment record written by
// deploy/deploy.py, so the page works with no query string at all.
let CONTRACT_ADDRESS = params.get("address") || "";

const ONE_GEN = 10n ** 18n;

let client = null;
let account = null;

const $ = (id) => document.getElementById(id);

function setStatus(node, text, kind = "") {
  node.textContent = text;
  node.className = `status ${kind}`;
}

function short(value, head = 6, tail = 4) {
  if (!value) return "";
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

function genToWei(text) {
  const [whole, fraction = ""] = String(text).trim().split(".");
  const padded = (fraction + "0".repeat(18)).slice(0, 18);
  return BigInt(whole || "0") * ONE_GEN + BigInt(padded || "0");
}

function weiToGen(value) {
  const wei = BigInt(value ?? 0);
  const whole = wei / ONE_GEN;
  const fraction = (wei % ONE_GEN).toString().padStart(18, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : `${whole}`;
}

async function connect() {
  if (!window.ethereum) {
    setStatus($("open-status"), "No injected wallet found.", "bad");
    return;
  }
  const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
  account = accounts[0];
  client = createClient({ chain: CHAIN, account, provider: window.ethereum });

  $("account").textContent = short(account);
  $("network").textContent = CHAIN_LABEL;
  $("network").className = "pill pill-ok";
  await refresh();
}

function ensureClient() {
  if (client) return true;
  // Reads work without a wallet, so browsing stays available.
  client = createClient({ chain: CHAIN });
  return true;
}

async function read(functionName, args = []) {
  ensureClient();
  return client.readContract({
    address: CONTRACT_ADDRESS,
    functionName,
    args,
  });
}

async function write(functionName, args = [], value = 0n) {
  if (!account) throw new Error("connect a wallet first");
  const hash = await client.writeContract({
    address: CONTRACT_ADDRESS,
    functionName,
    args,
    value,
  });
  await client.waitForTransactionReceipt({ hash });
  return hash;
}

async function loadOracleConfig() {
  const config = await read("get_config");
  const box = $("oracle");
  const items = [
    ["Validator tolerance", `${config.validator_tolerance_bps} bps`],
    ["Cross-source agreement", `${config.source_agreement_bps} bps`],
    ["Liquidity floor", `$${Number(config.min_liquidity_usd).toLocaleString()}`],
    ["Matching window", `${config.match_window_seconds / 3600} h`],
    ["Wars opened", config.total_wars],
    ["Wars settled", config.total_settled],
  ];
  box.innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="stat"><span class="muted">${label}</span><strong>${value}</strong></div>`,
    )
    .join("");
}

function warCard(war, { joinable = false, mine = false } = {}) {
  const entry = war.entry_price ? weiToGen(war.entry_price) : "—";
  const exit = war.exit_price ? weiToGen(war.exit_price) : "—";
  const when = war.resolve_at
    ? new Date(Number(war.resolve_at) * 1000).toISOString().replace("T", " ").slice(0, 16)
    : "—";

  const actions = [];
  if (joinable && war.status === "OPEN") {
    actions.push(`<button class="btn btn-primary" data-action="join" data-war="${war.war_id}">Join ${weiToGen(war.stake)} GEN</button>`);
  }
  if (mine && war.status === "OPEN") {
    actions.push(`<button class="btn" data-action="cancel" data-war="${war.war_id}">Cancel</button>`);
  }
  if (mine && war.status === "MATCHED") {
    actions.push(`<button class="btn" data-action="resolve" data-war="${war.war_id}">Resolve</button>`);
  }

  return `
    <article class="war">
      <header>
        <strong>${war.token_symbol}</strong>
        <span class="pill pill-${war.status === "OPEN" ? "warn" : war.status === "MATCHED" ? "ok" : "muted"}">${war.status}</span>
        <span class="muted">${war.timeframe} · ${war.creator_side}</span>
      </header>
      <dl class="mono">
        <div><dt>stake</dt><dd>${weiToGen(war.stake)} GEN</dd></div>
        <div><dt>pot</dt><dd>${weiToGen(war.pot)} GEN</dd></div>
        <div><dt>entry</dt><dd>${entry}</dd></div>
        <div><dt>exit</dt><dd>${exit}</dd></div>
        <div><dt>resolves</dt><dd>${when}</dd></div>
        <div><dt>winner</dt><dd>${war.winner || "—"}</dd></div>
      </dl>
      <footer>${actions.join("")}</footer>
    </article>`;
}

async function loadDeploymentRecord() {
  if (CONTRACT_ADDRESS) return;
  try {
    const response = await fetch("./deployment.json", { cache: "no-store" });
    if (!response.ok) return;
    const record = await response.json();
    if (record.address) CONTRACT_ADDRESS = record.address;
  } catch (error) {
    // No record served alongside the page; the query string is the fallback.
  }
}

async function refresh() {
  await loadDeploymentRecord();
  if (!CONTRACT_ADDRESS) {
    $("wars").innerHTML = `<p class="muted">Set the contract address with ?address=0x… or deploy first.</p>`;
    return;
  }
  try {
    await loadOracleConfig();

    const open = await read("list_open", [20]);
    $("wars").innerHTML = open.length
      ? open.map((war) => warCard(war, { joinable: true })).join("")
      : `<p class="muted">No open wars. Be the first.</p>`;

    if (account) {
      const mine = await read("list_by_owner", [account, 20]);
      $("mine").innerHTML = mine.length
        ? mine.map((war) => warCard(war, { mine: true })).join("")
        : `<p class="muted">You have no wars yet.</p>`;

      const claimable = await read("get_claim", [account]);
      $("claimable").textContent = `Claimable: ${weiToGen(claimable)} GEN`;
    }
  } catch (error) {
    $("wars").innerHTML = `<p class="status bad">${error.message}</p>`;
  }
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, war } = button.dataset;
  const status = $("open-status");
  try {
    if (action === "join") {
      const target = (await read("get_war", [war])).stake;
      setStatus(status, "Submitting match…");
      await write("join_war", [war], BigInt(target));
    } else if (action === "cancel") {
      setStatus(status, "Cancelling…");
      await write("cancel_open_war", [war]);
    } else if (action === "resolve") {
      setStatus(status, "Asking validators to settle…");
      await write("resolve_war", [war]);
    }
    setStatus(status, "Done.", "ok");
    await refresh();
  } catch (error) {
    setStatus(status, error.message, "bad");
  }
});

$("connect").addEventListener("click", () => connect().catch((e) => setStatus($("open-status"), e.message, "bad")));
$("refresh").addEventListener("click", () => refresh());

$("claim").addEventListener("click", async () => {
  const status = $("open-status");
  try {
    setStatus(status, "Claiming…");
    await write("claim");
    setStatus(status, "Claimed.", "ok");
    await refresh();
  } catch (error) {
    setStatus(status, error.message, "bad");
  }
});

$("open-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("open-status");
  const data = new FormData(event.target);
  try {
    setStatus(status, "Locking your stake…");
    await write(
      "create_war",
      [data.get("symbol"), data.get("address"), data.get("chain"), data.get("timeframe"), data.get("side")],
      genToWei(data.get("stake")),
    );
    setStatus(status, "War opened.", "ok");
    await refresh();
  } catch (error) {
    setStatus(status, error.message, "bad");
  }
});

$("parse-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = new FormData(event.target).get("value");
  try {
    const sample = await read("preview_sample", [value]);
    $("parse-out").textContent = JSON.stringify(sample, null, 2);
  } catch (error) {
    $("parse-out").textContent = error.message;
  }
});

$("contract-line").textContent = CONTRACT_ADDRESS
  ? `MemeWar · ${CONTRACT_ADDRESS} · ${CHAIN_LABEL}`
  : "MemeWar · not deployed yet";

refresh();
