const form = document.querySelector("#analysis-form");
const statusBox = document.querySelector("#status");
const result = document.querySelector("#result");
const resultBody = document.querySelector("#result-body");

function setStatus(message, tone = "") {
  statusBox.textContent = message;
  statusBox.className = `status ${tone}`;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const version = new FormData(form).get("version");
  setStatus("Validando versión y comprobando la configuración…");
  result.classList.add("hidden");
  try {
    const response = await fetch("/api/analyze/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudo completar el análisis.");
    setStatus("Informe creado. Revisa las fuentes antes de decidir.", "success");
    resultBody.innerHTML = `<p><strong>Estado:</strong> ${data.status}</p><p><a class="button-link" href="${data.report_url}">Abrir informe HTML</a> <a class="button-link" href="${data.json_url}">Descargar JSON</a></p>`;
    result.classList.remove("hidden");
  } catch (error) {
    setStatus(error.message, "error");
  }
});
