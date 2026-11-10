const form = document.querySelector("#analysis-form");
const statusBox = document.querySelector("#status");
const csrf = () => document.querySelector("[name=csrfmiddlewaretoken]").value;
function status(message) { if (statusBox) statusBox.textContent = message; }
async function post(url, data = {}) {
  const response = await fetch(url, {method:"POST", mode:"same-origin", headers:{"Content-Type":"application/json","X-CSRFToken":csrf()},body:JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(value.error || "No se confirmó la operación. Consulta el informe antes de repetir.");
    error.code = value.code;
    throw error;
  }
  return value;
}
if(form) form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button"); button.disabled = true;
  let key = form.dataset.requestId || crypto.randomUUID();
  form.dataset.requestId = key;
  try {
    status("Preparando un informe persistente…");
    const job = await post("/api/jobs/", {version:new FormData(form).get("version"),idempotency_key:key});
    try { await post("/api/jobs/" + job.id + "/sources/"); } catch(error) { status(error.message); }
    location.assign(job.url);
  } catch(error) { status(error.message); button.disabled = false; }
});
const jobNode = document.querySelector("#job-data");
if(jobNode) {
  const job = JSON.parse(jobNode.textContent);
  const resumeClose = document.querySelector("#resume-close");
  if(resumeClose) resumeClose.addEventListener("click", async () => {
    resumeClose.disabled = true;
    try {
      status("Comprobando el cierre guardado…");
      await post("/api/jobs/" + job.id + "/close-unverified/", {
        reason:job.unverified_closure.reason, confirmed:true,
        state_token:job.verification.state_token, idempotency_key:job.unverified_closure.id});
      location.assign("/");
    } catch(error) { status(error.message); resumeClose.disabled = false; }
  });
  const closure = document.querySelector("#close-unverified-form");
  if(closure) closure.addEventListener("submit", async event => {
    event.preventDefault();
    const button = closure.querySelector("button"); button.disabled = true;
    closure.dataset.requestId ||= crypto.randomUUID();
    try {
      status("Cerrando el informe sin verificar…");
      await post("/api/jobs/" + job.id + "/close-unverified/", {
        reason:document.querySelector("#closure-reason").value,
        confirmed:document.querySelector("#closure-confirmed").checked,
        state_token:job.verification.state_token, idempotency_key:closure.dataset.requestId});
      location.reload();
    } catch(error) { status(error.message); button.disabled = false; }
  });
  document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", async () => {
    button.disabled = true;
    try { status("Procesando la etapa solicitada…"); await post("/api/jobs/" + job.id + "/" + button.dataset.action + "/"); location.reload(); }
    catch(error) {
      if (["grounding_response_unavailable", "grounding_recovery_required"].includes(error.code)) location.reload();
      else { status(error.message); button.disabled = false; }
    }
  }));
  const review = document.querySelector("#review-form");
  const draft = structuredClone(job.review_proposal || {});
  const confirmed = new Set();
  function unconfirm(index) {
    confirmed.delete(index);
    const row = document.querySelector(`[data-issue-row="${index}"]`);
    row.querySelector("[data-confirm-status]").textContent = "Pendiente de confirmar";
    row.querySelector("[data-confirm]").textContent = "Confirmar aviso";
  }
  document.querySelectorAll("[data-edit]").forEach(button => button.addEventListener("click", () => {
    const editor = document.getElementById("edit-" + button.dataset.edit);
    editor.hidden = !editor.hidden;
    button.setAttribute("aria-expanded", String(!editor.hidden));
    if (!editor.hidden) unconfirm(Number(button.dataset.edit));
  }));
  document.querySelectorAll("[data-confirm]").forEach(button => button.addEventListener("click", () => {
    const index = Number(button.dataset.confirm);
    confirmed.add(index);
    button.textContent = "Confirmado";
    button.closest("tr").querySelector("[data-confirm-status]").textContent = "Confirmado para guardar";
  }));
  document.querySelectorAll("[data-issue-row] [data-field]").forEach(input => input.addEventListener("input", () => {
    const index = Number(input.closest("[data-issue-row]").dataset.issueRow);
    const group = input.closest("fieldset");
    const branch = group.hasAttribute("data-branch");
    const item = draft.issues[index][branch ? "branches" : "conditions"][Number(branch ? group.dataset.branch : group.dataset.condition)];
    item[input.dataset.field] = input.value || (branch && ["affected_from", "affected_to", "fixed"].includes(input.dataset.field) ? null : "");
    unconfirm(index);
  }));
  if(review) review.addEventListener("submit", async event => {
    event.preventDefault();
    const button = review.querySelector("button"); button.disabled = true;
    try {
      if (confirmed.size !== draft.issues.length) throw new Error("Confirma cada aviso de la tabla antes de guardar.");
      draft.summary = document.querySelector("#review-summary").value;
      const support = document.querySelector("#support-state").value;
      draft.support = {supported:support === "unknown" ? null : support === "true", source_url:document.querySelector("#support-url").value || null, quote:document.querySelector("#support-quote").value};
      draft.uncertainty = document.querySelector("#review-uncertainty").value.split("\n").map(s => s.trim()).filter(Boolean);
      await post("/api/jobs/" + job.id + "/review/", {
      evidence:draft,
      reason:document.querySelector("#reason").value,
      review_version:Number(document.querySelector("#review-version").value)
    }); location.reload(); } catch(error) { status(error.message); button.disabled = false; }
  });
}
