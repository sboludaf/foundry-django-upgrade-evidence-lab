const form = document.querySelector("#analysis-form");
const statusBox = document.querySelector("#status");
const csrf = () => document.querySelector("[name=csrfmiddlewaretoken]").value;
function status(message) { if (statusBox) statusBox.textContent = message; }
async function post(url, data = {}) {
  const response = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json","X-CSRFToken":csrf()},body:JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "No se confirmó la operación. Consulta el informe antes de repetir.");
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
  document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", async () => {
    button.disabled = true;
    try { status("Procesando la etapa solicitada…"); await post("/api/jobs/" + job.id + "/" + button.dataset.action + "/"); location.reload(); }
    catch(error) { status(error.message); button.disabled = false; }
  }));
  const review = document.querySelector("#review-form");
  if(review) review.addEventListener("submit", async event => {
    event.preventDefault();
    const button = review.querySelector("button"); button.disabled = true;
    try { await post("/api/jobs/" + job.id + "/review/", {
      evidence:JSON.parse(document.querySelector("#evidence").value),
      reason:document.querySelector("#reason").value,
      review_version:Number(document.querySelector("#review-version").value)
    }); location.reload(); } catch(error) { status(error.message); button.disabled = false; }
  });
}
