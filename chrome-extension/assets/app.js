"use strict";
const $ = id => document.getElementById(id);
const labels = {voice: "Voice and attitude", rhythm: "Sentence rhythm", rhetoric: "Expression and detail",
  structure: "Movement and ending", economy: "Freedom from filler", facts_preserved: "Meaning preserved",
  no_invented_facts: "No invented facts"};
const versionName = id => ({original: "Original draft", improved: "Luna · expression improved",
  rewrite_1: "Style pass 1", rewrite_2: "Style pass 2"}[id] || id);
let session, context = "", busy = false, posts = [], selected = new Set(), lastJob = null;
let pollTimer, lastRender = "";
let hydrating = true;
const host = window.mimicryHost;
const modeLabels = {refine_personalize: "Refine & personalize", personalize: "Personalize my draft", refine: "Refine my draft"};
if (host) document.body.classList.add("chrome-sidepanel");

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(message, target = "notice") {
  $(target).textContent = message || "";
  $(target).hidden = !message;
}
async function api(path, data) {
  const options = data === undefined ? {} : {method: "POST",
    headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf}, body: JSON.stringify(data)};
  const response = await fetch((host?.apiBase || "") + path, {...options, credentials: "include"});
  let result;
  try { result = await response.json(); } catch { throw new Error("The local server is unavailable. Reload the page."); }
  if (!response.ok) throw new Error(result.error || "This request failed. Try again.");
  return result;
}
function formData() {
  return {references: $("references").value, source_text: $("draft").value,
    output_format: $("format").value, feed_context: context, writing_mode: $("writing-mode").value};
}
function validate() {
  const mode = $("writing-mode").value;
  const needsVoice = mode !== "refine";
  const count = Array.from($("references").value.trim()).length;
  const draftCount = Array.from($("draft").value.trim()).length;
  $("reference-help").textContent = count < 80 ? `${count} characters · add ${80-count} more to reach 80.` :
    `${count.toLocaleString()} characters · ${selected.size} tweets selected. You can edit these samples.`;
  $("draft-help").textContent = draftCount < 10 ? `Write at least ${10-draftCount} more characters.` :
    `${draftCount.toLocaleString()} characters · this draft stays the meaning anchor.`;
  $("rewrite").disabled = busy || !session?.models_ready || (needsVoice && (count < 80 || count > 12000)) || draftCount < 10 || draftCount > 12000;
  $("connect").disabled = !session?.x_configured || busy;
  $("voice-ready").textContent = count >= 80 && count <= 12000 ? "Ready" : "Set up";
  $("open-voice").classList.toggle("voice-ready", count >= 80 && count <= 12000);
  if (!busy) $("rewrite").textContent = needsVoice && count < 80 ? "Add your voice to start" : modeLabels[mode];
  $("feed-connect").disabled = !session?.x_configured || busy;
  const stale = lastJob?.result && (lastJob.result.source_text !== $("draft").value || (needsVoice && lastJob.result.references !== $("references").value) || lastJob.result.feed_context !== context || lastJob.result.output_format !== $("format").value || (lastJob.result.writing_mode || "refine_personalize") !== mode);
  $("result-note").hidden = !stale;
  $("result-note").textContent = stale ? "These results are from your previous inputs. Run again to use your latest changes." : "";
  for (const id of ["draft", "references", "format", "writing-mode", "example", "use-context", "clear-context"]) $(id).disabled = busy;
  for (const node of document.querySelectorAll(".sample input, .feed-card button")) node.disabled = busy;
  if (!hydrating) host?.saveDraft(formData());
  $("flow-rewrite").hidden = mode === "personalize"; $("flow-voice").hidden = mode === "refine";
  $("flow-voice").querySelector("span").textContent = mode === "personalize" ? "2" : "3";
  const displayMode = lastJob?.result?.writing_mode || mode;
  document.querySelector(".ai-card").hidden = displayMode !== "refine_personalize";
  document.querySelector(".stage-connector").hidden = displayMode !== "refine_personalize";
  $("final-heading").textContent = displayMode === "refine" ? "Refined draft" : "In your voice";
  $("final-number").textContent = displayMode === "refine_personalize" ? "03" : "02";
  if (!lastJob) {
    $("final-status").textContent = mode === "refine" ? "Clearer expression" : "Personalized to you";
    $("kept").replaceChildren(element("p", mode === "refine" ?
      "Your idea, expressed clearly. Same meaning." :
      "Your rhythm, word choice, and personality. Same point.", "output-text placeholder"));
  }
}
function showCriteria() {
  if (!session?.criteria) return;
  $("criteria").replaceChildren();
  for (const [key, question] of Object.entries(session.criteria[$("format").value])) {
    if ($("writing-mode").value === "refine" && !["facts_preserved", "no_invented_facts"].includes(key)) continue;
    const detail = element("details");
    detail.append(element("summary", labels[key]), element("p", question.instructions));
    const rubric = element("pre", JSON.stringify(question.criteria, null, 2));
    detail.append(rubric);
    $("criteria").append(detail);
  }
}
function showPosts(data) {
  posts = data.posts;
  selected = new Set([...selected].filter(id => posts.some(p => p.id === id)));
  $("posts").replaceChildren();
  $("posts-status").textContent = posts.length ? `${posts.length} standalone tweets from @${session.user.username}. Select the ones you wrote yourself.` :
    "No standalone tweets on this page. Paste your own writing below.";
  if (data.has_more) $("posts-status").textContent += " Showing one recent page.";
  for (const post of posts) {
    const row = element("div", undefined, "sample");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.id = "sample-" + post.id; checkbox.checked = selected.has(post.id);
    const label = element("label", post.text); label.htmlFor = checkbox.id;
    const link = element("a", "View on X ↗"); link.href = post.url; link.target = "_blank"; link.rel = "noreferrer";
    const text = element("div"); text.append(label, link); row.append(checkbox, text);
    checkbox.addEventListener("change", () => {
      checkbox.checked ? selected.add(post.id) : selected.delete(post.id);
      const references = posts.filter(p => selected.has(p.id)).map(p => p.text).join("\n\n---\n\n");
      if (Array.from(references).length > 12000) {
        selected.delete(post.id); checkbox.checked = false;
        notice("These samples exceed 12,000 characters. Select fewer tweets."); return;
      }
      $("references").value = references; validate();
    });
    $("posts").append(row);
  }
}
function setContext(value) {
  context = value;
  $("context-box").hidden = !value;
  $("context-text").textContent = value;
  if (session) validate();
}
function showFeed(data) {
  $("feed").replaceChildren();
  $("feed-empty").hidden = data.posts.length > 0;
  $("feed-status").textContent = data.posts.length ? "Recent home timeline · chronological. Choose a post to write about." :
    "No posts on this page. You can still write your own thought below.";
  for (const post of data.posts) {
    const card = element("article", undefined, "feed-card");
    const author = element("a", post.name || "@" + (post.username || "X user"));
    author.href = post.url; author.target = "_blank"; author.rel = "noreferrer";
    const authorRow = element("div", undefined, "feed-author");
    const avatar = element("span", (post.name || post.username || "X").slice(0, 1).toUpperCase(), "avatar");
    avatar.setAttribute("aria-hidden", "true");
    const identity = element("div");
    identity.append(author, element("div", "@" + (post.username || "X user"), "feed-date"));
    authorRow.append(avatar, identity);
    const button = element("button", "Add my take ↗");
    button.addEventListener("click", () => {
      const text = `Post by @${post.username}:\n${post.text}`;
      if (Array.from(text).length > 12000) { notice("This post exceeds the context limit. Write your own summary in the draft instead."); return; }
      for (const other of $("feed").children) other.classList.remove("selected");
      card.classList.add("selected");
      setContext(text); $("draft").focus();
    });
    card.append(authorRow, element("p", post.text), button); $("feed").append(card);
  }
}
async function loadTimeline(kind) {
  const owner = session;
  const button = $("load-" + kind), status = $(kind + "-status");
  button.disabled = true; status.textContent = "Loading from X…";
  try {
    const data = await api("/api/x/" + kind, {});
    if (session !== owner) return;
    if (kind === "posts") showPosts(data); else showFeed(data);
  } catch (error) { if (session === owner) status.textContent = error.message; }
  finally { if (session === owner) button.disabled = false; }
}
function resetResult() {
  lastJob = null; lastRender = "";
  $("result-meta").hidden = true; $("result-note").hidden = true;
  $("loop-details").hidden = true; $("loop-details").open = false;
  for (const id of ["steps", "versions", "raw-result"]) $(id).replaceChildren();
  $("ai-output").textContent = "Your idea, organized into a clear draft.";
  $("ai-output").classList.add("placeholder");
  $("final-status").textContent = "Personalized to you";
  $("kept").replaceChildren(element("p", "Your rhythm, word choice, and personality. Same point.", "output-text placeholder"));
  for (const name of ["thought", "rewrite", "voice"]) {
    $("flow-" + name).classList.toggle("active", name === "thought");
    $("flow-" + name).classList.remove("complete");
  }
}
function ratings(judgment) {
  if (!judgment) return element("p", "Awaiting TypeSafe evaluation.", "helper");
  const table = element("table", undefined, "rating-table");
  const head = element("tr"); head.append(element("th", "TypeSafe judgment"), element("th", "Value")); table.append(head);
  const content = {facts_preserved: judgment.facts_preserved, no_invented_facts: judgment.no_invented_facts};
  const entries = {...judgment.style, economy: judgment.economy, ...content};
  for (const [key, value] of Object.entries(entries)) {
    if (value == null && !(key in content)) continue;
    const row = element("tr"), score = typeof value === "number" ? value.toFixed(2) : "—";
    row.append(element("td", labels[key] || key), element("td", score + (key in content ? " P(yes)" : " / 4")));
    table.append(row);
  }
  return table;
}
function drawResult(job) {
  lastJob = job;
  $("result-panel").hidden = false;
  $("result-meta").hidden = false;
  $("run-status").textContent = job.status;
  $("progress").textContent = job.progress;
  const result = job.result;
  const mode = result?.writing_mode || $("writing-mode").value;
  const stage = mode === "personalize" ? "voice" : mode === "refine" || !result?.versions.improved ? "rewrite" : "voice";
  for (const name of ["thought", "rewrite", "voice"]) {
    $("flow-" + name).classList.toggle("active", name === stage);
    $("flow-" + name).classList.toggle("complete", name === "thought" || (name === "rewrite" && stage === "voice"));
  }
  $("rewrite").textContent = job.status === "running" ? (stage === "rewrite" ? "Refining your draft…" : "Matching your voice…") : modeLabels[mode];
  if (!result) return;
  const signature = job.status + JSON.stringify(result);
  if (signature === lastRender) return;
  lastRender = signature;
  $("loop-details").hidden = false;
  $("ai-output").textContent = result.versions.improved?.text || (job.status === "running" ? "Luna is organizing your thought…" : "The AI rewrite could not be completed. Your original thought was preserved.");
  $("ai-output").classList.toggle("placeholder", !result.versions.improved);
  $("steps").replaceChildren();
  for (const step of result.steps) {
    const node = element("div", undefined, "step " + (step.accepted ? "accepted" : "rejected"));
    node.append(element("strong", versionName(step.version) + (step.accepted ? " · kept" : " · rejected")), element("span", step.reason));
    $("steps").append(node);
  }
  $("kept").replaceChildren();
  const kept = result.versions[result.selected];
  const heading = element("div", undefined, "copy-row");
  const copy = element("button", "Copy text", "quiet");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(kept.text); copy.textContent = "Copied"; }
    catch { notice("Copy is unavailable. Select and copy the text below."); }
  });
  const hasStyle = mode === "refine" ? result.selected === "improved" : result.selected.startsWith("rewrite_");
  $("final-status").textContent = job.status === "running" ? (mode === "refine" ? "Checking your meaning…" : "Finding your voice…") : hasStyle ? "Kept after checks" : "Earlier version kept";
  if (job.status === "running" && !hasStyle) {
    $("kept").append(element("p", mode === "refine" ? "Improving expression and checking that your meaning stays intact." : "Comparing against your writing samples and checking each edit.", "output-text placeholder"));
  } else {
    $("kept").append(element("p", kept.text, "output-text"));
    const count = kept.tweet_check ? `${kept.tweet_check.weighted_length} / 280 · ${kept.tweet_check.valid ? "fits one tweet" : "over the tweet limit"}` : versionName(result.selected);
    heading.append(element("span", count, "helper"), copy); $("kept").append(heading);
    if (!hasStyle) $("kept").append(element("p", mode === "refine" ? "The refinement did not qualify for replacement. The original was preserved." : "No style rewrite passed every check. This earlier version was preserved.", "helper"));
  }
  if (result.error) $("kept").append(element("p", result.error, "loop-warning"));
  $("versions").replaceChildren();
  for (const [id, version] of Object.entries(result.versions)) {
    const detail = element("details", undefined, "version");
    detail.append(element("summary", versionName(id) + (id === result.selected ? " · selected" : "")));
    detail.append(element("p", version.text, "kept-text"), ratings(version.judgment));
    if (id !== "original") {
      detail.append(element("p", "Changes from your original · removed text is struck through; additions are underlined.", "diff-key"));
      const diff = element("div");
      // This markup comes only from the server's HTML-escaping diff renderer.
      diff.innerHTML = job.diffs[id]; detail.append(diff);
    }
    if (version.judgment?.answers) {
      const raw = element("details"); raw.append(element("summary", "Raw TypeSafe answers and probabilities"), element("pre", JSON.stringify(version.judgment.answers, null, 2))); detail.append(raw);
    }
    $("versions").append(detail);
  }
  $("raw-result").textContent = JSON.stringify(result, null, 2);
}
async function poll(id, failures = 0) {
  const owner = session;
  clearTimeout(pollTimer);
  try {
    const job = await api("/api/jobs/" + encodeURIComponent(id));
    if (session !== owner) return;
    drawResult(job); busy = job.status === "running"; validate();
    if (busy) pollTimer = setTimeout(() => poll(id), 1500);
  } catch (error) {
    if (session !== owner) return;
    $("progress").textContent = error.message;
    if (failures < 2) pollTimer = setTimeout(() => poll(id, failures + 1), 2000);
    else notice("Progress disconnected. Reload to reconnect; the server may still be working.");
  }
}
$("connect").addEventListener("click", async () => {
  $("connect").disabled = true; $("connect").textContent = "Opening X…"; notice("");
  try {
    const data = await api("/auth/x/start", formData());
    if (host) { await host.openAuth(data.url); $("connect").textContent = "Connect X"; validate(); }
    else window.location.assign(data.url);
  }
  catch (error) { notice(error.message); $("connect").textContent = "Connect X"; validate(); }
});
$("logout").addEventListener("click", async () => {
  $("logout").disabled = true;
  try {
    const result = await api("/auth/x/logout", {});
    await host?.clearDraft();
    clearTimeout(pollTimer); session = null; posts = []; selected.clear(); busy = false; lastJob = null; lastRender = "";
    $("references").value = ""; $("draft").value = ""; setContext("");
    $("posts").replaceChildren(); $("feed").replaceChildren(); $("feed-empty").hidden = false;
    $("pasted-context").value = ""; resetResult();
    await boot(); notice(result.message);
  } catch (error) { notice(error.message); }
  finally { $("logout").disabled = false; }
});
$("load-posts").addEventListener("click", () => loadTimeline("posts"));
$("load-feed").addEventListener("click", () => loadTimeline("feed"));
$("clear-context").addEventListener("click", () => setContext(""));
$("open-voice").addEventListener("click", () => $("voice-dialog").showModal());
$("close-voice").addEventListener("click", async () => {
  await host?.saveNow(formData()); $("voice-dialog").close();
});
$("feed-connect").addEventListener("click", () => $("connect").click());
$("paste-context").addEventListener("click", () => $("context-dialog").showModal());
$("close-context").addEventListener("click", () => $("context-dialog").close());
$("use-context").addEventListener("click", () => {
  setContext($("pasted-context").value.trim()); $("context-dialog").close(); $("draft").focus();
});
$("references").addEventListener("input", validate);
$("draft").addEventListener("input", validate);
$("format").addEventListener("change", () => { showCriteria(); validate(); });
$("writing-mode").addEventListener("change", () => { showCriteria(); validate(); });
$("example").addEventListener("click", () => {
  if (!session) return;
  selected.clear(); for (const node of $("posts").querySelectorAll("input")) node.checked = false;
  $("references").value = session.examples.references; $("draft").value = session.examples.source_text;
  setContext(""); notice("Fictional samples loaded. Replace them with your own writing whenever you like."); validate();
});
$("rewrite").addEventListener("click", async () => {
  busy = true; validate(); notice("", "form-error");
  try {
    const job = await api("/api/rewrite", formData());
    resetResult();
    $("ai-output").textContent = "Luna is organizing your thought…"; $("ai-output").classList.add("placeholder");
    $("raw-result").textContent = ""; drawResult({status: "running", progress: "Starting Luna…", result: null});
    await poll(job.id);
  } catch (error) { notice(error.message, "form-error"); busy = false; validate(); }
});
async function boot() {
  const localDraft = session && host ? formData() : null;
  hydrating = true;
  session = await api("/api/session");
  const signedIn = Boolean(session.user);
  $("account-label").textContent = signedIn ? `@${session.user.username}` : "Local writing studio";
  $("connect").hidden = signedIn; $("connect").textContent = "Connect X";
  $("logout").hidden = !signedIn;
  for (const kind of ["posts", "feed"]) $("load-" + kind).hidden = !signedIn;
  if (session.workspace.references !== undefined) {
    $("references").value = session.workspace.references; $("draft").value = session.workspace.source_text;
    $("format").value = session.workspace.output_format; setContext(session.workspace.feed_context);
    $("writing-mode").value = session.workspace.writing_mode || "refine_personalize";
  }
  const invalid = new URLSearchParams(location.search).has("signin");
  if (!host) history.replaceState(null, "", "/");
  notice(session.notice || (invalid ? "That X callback is no longer valid. Click Connect X to start again." : ""));
  if (!session.x_configured) notice("X login needs X_CLIENT_ID and X_CLIENT_SECRET in the server .env file.");
  if (!session.models_ready) notice("Add OPENAI_API_KEY and TYPESAFE_API_KEY to the server .env to enable rewriting.", "form-error");
  const savedDraft = localDraft || await host?.loadDraft();
  if (savedDraft) {
    $("references").value = savedDraft.references || (session.voice_profile ? session.workspace.references : "") || ""; $("draft").value = savedDraft.source_text || "";
    $("format").value = savedDraft.output_format || "tweet"; setContext(savedDraft.feed_context || "");
    $("writing-mode").value = savedDraft.writing_mode || "refine_personalize";
  }
  hydrating = false;
  showCriteria(); validate();
  $("feed-connect").hidden = signedIn;
  if (signedIn) {
    if (session.posts) showPosts(session.posts); else void loadTimeline("posts");
    if (!host) { if (session.feed) showFeed(session.feed); else void loadTimeline("feed"); }
  } else {
    $("posts-status").textContent = session.voice_profile ? `Imported from the official X API for @${session.voice_profile.username}: ${session.voice_profile.authored_count} authored samples. ${session.voice_profile.repost_count} reposts are stored separately as preference context.` : "Connect X to see your own tweets, or paste your writing below.";
    $("feed-status").textContent = "Connect X to load your chronological home timeline. Read access only.";
  }
  if (session.job_id) { busy = true; validate(); await poll(session.job_id); }
  await host?.ready();
}
window.addEventListener("mimicry:context", event => {
  if (busy) { notice("Wait for this rewrite to finish, then choose the post again."); return; }
  const value = event.detail?.text;
  if (typeof value !== "string" || Array.from(value).length > 12000) return;
  setContext(value); $("draft").focus(); notice("Post selected. Write your own take below.");
});
window.addEventListener("mimicry:session", () => { if (!busy) boot().catch(error => notice(error.message)); });
boot().catch(error => notice(error.message));
